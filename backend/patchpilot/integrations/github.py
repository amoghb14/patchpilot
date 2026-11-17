"""GitHub integration: import a repository and open a migration pull request.

Uses only the REST API with a server-side token. The token is never returned to
a client, logged, or written into the workspace.
"""

from __future__ import annotations

import base64
import io
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(RuntimeError):
    """Raised when GitHub rejects a request."""


def parse_repo(full_name: str) -> tuple[str, str]:
    if not REPO_RE.match(full_name):
        raise GitHubError("repository must look like 'owner/repo'")
    owner, repo = full_name.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."}:
        raise GitHubError("invalid repository name")
    return owner, repo


@dataclass
class GitHubClient:
    token: str | None
    api_base: str = "https://api.github.com"
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        try:
            response = httpx.request(
                method, url, headers=self._headers(), timeout=self.timeout, follow_redirects=True, **kwargs
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("message", "")
            except ValueError:
                detail = response.text[:200]
            raise GitHubError(f"GitHub {method} {url} -> {response.status_code}: {detail}")
        return response

    # -- import ----------------------------------------------------------
    def download_repository(self, full_name: str, destination: Path, ref: str | None = None,
                            max_bytes: int = 40_000_000, max_files: int = 3000) -> dict:
        owner, repo = parse_repo(full_name)
        target = f"/repos/{owner}/{repo}/tarball/{ref}" if ref else f"/repos/{owner}/{repo}/tarball"
        response = self._request("GET", target)
        payload = response.content
        if len(payload) > max_bytes:
            raise GitHubError("repository archive is too large to import")
        destination.mkdir(parents=True, exist_ok=True)
        extracted = 0
        root = destination.resolve()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                # Strip the single top-level directory GitHub adds.
                parts = Path(member.name).parts
                if len(parts) < 2:
                    continue
                relative = Path(*parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                out_path = (root / relative).resolve()
                if root not in out_path.parents:
                    continue
                if member.size > 2_000_000:
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                out_path.write_bytes(source.read(2_000_001))
                extracted += 1
                if extracted >= max_files:
                    break
        return {"path": str(root), "files": extracted, "repo": f"{owner}/{repo}"}

    # -- pull request ----------------------------------------------------
    def create_pull_request(
        self,
        full_name: str,
        branch: str,
        base_branch: str,
        files: dict[str, str],
        title: str,
        body: str,
        commit_message: str,
    ) -> dict:
        if not self.token:
            raise GitHubError(
                "no GitHub token configured; set PATCHPILOT_GITHUB_TOKEN to open pull requests"
            )
        owner, repo = parse_repo(full_name)
        base_ref = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{base_branch}").json()
        base_sha = base_ref["object"]["sha"]
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        for path, content in files.items():
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            existing_sha = None
            probe = httpx.request(
                "GET",
                f"{self.api_base}/repos/{owner}/{repo}/contents/{path}",
                headers=self._headers(),
                params={"ref": branch},
                timeout=self.timeout,
            )
            if probe.status_code == 200:
                existing_sha = probe.json().get("sha")
            payload = {"message": commit_message, "content": encoded, "branch": branch}
            if existing_sha:
                payload["sha"] = existing_sha
            self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=payload)
        pull = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": branch, "base": base_branch, "body": body},
        ).json()
        return {"number": pull.get("number"), "url": pull.get("html_url"), "branch": branch}
