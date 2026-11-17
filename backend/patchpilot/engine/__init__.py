"""PatchPilot analysis engine: spec diff, code analysis, migration, verification."""

from .analysis import scan_repository
from .migration import apply_plan_patch, plan_patch
from .openapi import MigrationMetadata, diff_models, normalize, summarize
from .verification import verify_patch

__all__ = [
    "MigrationMetadata",
    "apply_plan_patch",
    "diff_models",
    "normalize",
    "plan_patch",
    "scan_repository",
    "summarize",
    "verify_patch",
]
