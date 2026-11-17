"""Two-run verification: prove the breakage, then prove the patch fixes it.

A patch is only reported as ``verified`` when the unpatched repository fails the
suite under the new contract *and* the patched repository passes it. Anything
weaker is reported honestly as ``baseline_passed`` or ``patch_failed``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import TestRunResult, cleanup, prepare_workspace, run_command

DEFAULT_COMMAND = ["python", "-m", "pytest", "-q"]


@dataclass
class VerificationReport:
    verdict: str  # verified | baseline_passed | patch_failed | inconclusive
    summary: str
    baseline: TestRunResult
    patched: TestRunResult
    patched_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "baseline": self.baseline.as_dict(),
            "patched": self.patched.as_dict(),
            "patched_files": self.patched_files,
        }


def _verdict(baseline: TestRunResult, patched: TestRunResult) -> tuple[str, str]:
    if baseline.status == "passed":
        return (
            "baseline_passed",
            "The unpatched repository already passes under the new contract, so the patch cannot be credited. "
            "Point the suite at the new API version or add a failing case.",
        )
    if patched.status == "passed":
        return (
            "verified",
            f"Regression reproduced ({baseline.failed} failing, {baseline.errors} errors) and the patch passes "
            f"{patched.passed} tests in {patched.duration_ms} ms.",
        )
    if patched.status == "timeout":
        return ("patch_failed", f"The patched suite timed out after {patched.duration_ms} ms.")
    return (
        "patch_failed",
        f"The patch did not fix the suite: {patched.failed} failing, {patched.errors} errors remain.",
    )


def verify_patch(
    repo_root: Path,
    patched_files: dict[str, str],
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 180,
    keep_workspaces: bool = False,
) -> VerificationReport:
    command = command or list(DEFAULT_COMMAND)
    env = env or {}

    baseline_ws = prepare_workspace(Path(repo_root), prefix="patchpilot-baseline-")
    patched_ws = prepare_workspace(Path(repo_root), patched_files, prefix="patchpilot-patched-")
    try:
        baseline = run_command(baseline_ws, command, env, timeout)
        patched = run_command(patched_ws, command, env, timeout)
        verdict, summary = _verdict(baseline, patched)
        return VerificationReport(
            verdict=verdict,
            summary=summary,
            baseline=baseline,
            patched=patched,
            patched_files=sorted(patched_files),
        )
    finally:
        if not keep_workspaces:
            cleanup(baseline_ws)
            cleanup(patched_ws)


def run_suite(
    repo_root: Path, command: list[str] | None = None, env: dict[str, str] | None = None, timeout: int = 180
) -> TestRunResult:
    workspace = prepare_workspace(Path(repo_root), prefix="patchpilot-suite-")
    try:
        return run_command(workspace, command or list(DEFAULT_COMMAND), env, timeout)
    finally:
        cleanup(workspace)


def write_workspace_for_review(repo_root: Path, patched_files: dict[str, str]) -> Path:
    """Materialise a patched copy for a human to inspect (caller owns cleanup)."""
    return Path(tempfile.mkdtemp(prefix="patchpilot-review-")) if not patched_files else prepare_workspace(
        Path(repo_root), patched_files, prefix="patchpilot-review-"
    )
