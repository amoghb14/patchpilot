"""Migration planning and source-preserving transformation."""

from .planner import CodeEdit, PatchPlan, Skipped, plan_patch, rewrite_url_literal
from .transformer import AppliedEdit, TransformResult, apply_edits, apply_plan_patch

__all__ = [
    "AppliedEdit",
    "CodeEdit",
    "PatchPlan",
    "Skipped",
    "TransformResult",
    "apply_edits",
    "apply_plan_patch",
    "plan_patch",
    "rewrite_url_literal",
]
