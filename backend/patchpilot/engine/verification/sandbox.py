"""Run a project's test suite in a disposable copy of its repository.

Isolation here is process-level: a fresh temporary directory, an allow-listed
environment, a hard timeout, and the child started in its own session so the
whole process group can be killed. That is enough for a deterministic demo and
for local use, but it is *not* a security boundary: for untrusted repositories
run the same runner inside a container with no network and a read-only root.
The API surface only needs ``run_command`` to swap in a Docker executor.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
}
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "VIRTUAL_ENV",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
)
SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")


@dataclass
class TestRunResult:
    status: str  # passed | failed | error | timeout
    returncode: int | None
    duration_ms: int
    command: list[str]
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    output: str = ""
    workspace: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "returncode": self.returncode,
            "duration_ms": self.duration_ms,
            "command": self.command,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "output": self.output,
        }


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def parse_counts(output: str) -> dict[str, int]:
    """Parse a pytest summary, falling back to the per-test result lines.

    Some flag combinations (``-qq``) and plugins omit the summary line, so the
    count is reconstructed from ``FAILED``/``ERROR`` lines before giving up.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for match in SUMMARY_RE.finditer(output):
        kind = match.group(2)
        if kind == "error":
            kind = "errors"
        counts[kind] = int(match.group(1))
    if counts["failed"] == 0 and counts["errors"] == 0 and "passed" not in output:
        failed = len(re.findall(r"^FAILED ", output, re.MULTILINE))
        errors = len(re.findall(r"^ERROR ", output, re.MULTILINE))
        if failed or errors:
            counts["failed"], counts["errors"] = failed, errors
    return counts


def run_command(
    workdir: Path,
    command: list[str],
    env_extra: dict[str, str] | None = None,
    timeout: int = 180,
    max_output: int = 8000,
) -> TestRunResult:
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=str(workdir),
        env=build_env(env_extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover
            process.kill()
        output, _ = process.communicate()
    duration_ms = int((time.perf_counter() - started) * 1000)
    output = output or ""
    counts = parse_counts(output)
    if timed_out:
        status = "timeout"
    elif process.returncode == 0:
        status = "passed"
    elif counts["errors"]:
        status = "error"
    elif counts["failed"]:
        status = "failed"
    else:
        status = "error"
    return TestRunResult(
        status=status,
        returncode=process.returncode,
        duration_ms=duration_ms,
        command=command,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        output=output[-max_output:],
        workspace=str(workdir),
    )


def copy_repository(source: Path, destination: Path, max_files: int = 4000) -> int:
    source = Path(source).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(source)
        target_dir = destination / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            src = Path(dirpath) / filename
            try:
                if src.is_symlink() or not src.is_file() or src.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            shutil.copy2(src, target_dir / filename)
            copied += 1
            if copied >= max_files:
                return copied
    return copied


def prepare_workspace(source: Path, patched_files: dict[str, str] | None = None, prefix: str = "patchpilot-run-") -> Path:
    workspace = Path(tempfile.mkdtemp(prefix=prefix))
    copy_repository(Path(source), workspace)
    for rel, content in (patched_files or {}).items():
        target = (workspace / rel).resolve()
        if not str(target).startswith(str(workspace.resolve())):
            raise ValueError(f"refusing to write outside the workspace: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def cleanup(workspace: Path | str | None) -> None:
    if not workspace:
        return
    shutil.rmtree(workspace, ignore_errors=True)
