"""Turn matched findings into concrete, minimal code edits.

The planner never invents semantics. Every edit has to be justified by a change
the differ classified as auto-fixable *and* a call site or field access the
analyser proved is tied to that endpoint. Anything else becomes a review note,
which is what keeps the generated patch small enough to trust.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.matcher import Location, MatchedChange
from ..analysis.model import LiteralRef
from ..openapi.differ import (
    ADDITIVE,
    ENDPOINT_REMOVED,
    INFO,
    PARAM_REQUIRED_ADDED,
    PATH_CHANGED,
    REQUEST_FIELD_REQUIRED_ADDED,
    RESPONSE_FIELD_REMOVED,
    RESPONSE_FIELD_RENAMED,
    Change,
)

REPLACE_STRING = "replace_string"
REPLACE_ATTRIBUTE = "replace_attribute"
OPTIONAL_SUBSCRIPT = "optional_subscript"
INSERT_DICT_KEY = "insert_dict_key"
INSERT_CALL_KWARG = "insert_call_kwarg"


@dataclass(frozen=True)
class CodeEdit:
    id: str
    kind: str
    file: str
    line: int
    col: int
    change_id: str
    change_kind: str
    old_value: str | None = None
    new_value: str | None = None
    value_expr: str | None = None
    path: str | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "change_id": self.change_id,
            "change_kind": self.change_kind,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "value_expr": self.value_expr,
            "path": self.path,
            "note": self.note,
        }


@dataclass
class Skipped:
    change_id: str
    change_kind: str
    location: dict | None
    reason: str

    def as_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "change_kind": self.change_kind,
            "location": self.location,
            "reason": self.reason,
        }


@dataclass
class PatchPlan:
    edits: list[CodeEdit] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    by_file: dict[str, list[CodeEdit]] = field(default_factory=dict)

    def add(self, edit: CodeEdit) -> None:
        if any(
            existing.kind == edit.kind
            and existing.file == edit.file
            and existing.line == edit.line
            and existing.col == edit.col
            and existing.new_value == edit.new_value
            for existing in self.edits
        ):
            return
        self.edits.append(edit)
        self.by_file.setdefault(edit.file, []).append(edit)


def _edit_id(kind: str, file: str, line: int, col: int, change_id: str) -> str:
    return hashlib.sha1(f"{kind}|{file}|{line}|{col}|{change_id}".encode()).hexdigest()[:12]


def _static_segments(path: str) -> list[str]:
    return path.split("/")


def rewrite_url_literal(
    literals: tuple[LiteralRef, ...], old_path: str, new_path: str
) -> tuple[LiteralRef, str] | None:
    """Rewrite the path portion of a URL literal for an endpoint move.

    Only the first *static* segment that differs is substituted, so query
    strings, base URLs and interpolated ids are preserved.
    """
    old_parts = _static_segments(old_path)
    new_parts = _static_segments(new_path)
    old_segment = new_segment = None
    for old_part, new_part in zip(old_parts, new_parts):
        if old_part == new_part:
            continue
        if old_part.startswith("{") or new_part.startswith("{"):
            continue
        old_segment, new_segment = old_part, new_part
        break
    if old_segment is None or new_segment is None:
        return None
    for literal in literals:
        value = literal.value
        for pattern in (f"/{old_segment}/", f"/{old_segment}"):
            if pattern in value:
                return literal, value.replace(pattern, f"/{new_segment}/" if pattern.endswith("/") else f"/{new_segment}", 1)
    return None


def _plan_rename(matched: MatchedChange, plan: PatchPlan) -> None:
    change = matched.change
    old_leaf_path = change.meta.get("old_path") or change.field_path
    old_leaf = change.meta.get("old_leaf") or (change.field_path or "").rsplit(".", 1)[-1]
    new_leaf = change.meta.get("new_leaf") or (change.new or "").rsplit(".", 1)[-1]
    if not change.auto_fixable or change.confidence != "explicit":
        for location in matched.locations or [None]:
            plan.skipped.append(
                Skipped(
                    change.id,
                    change.kind,
                    location.as_dict() if location else None,
                    "rename is a hypothesis without migration metadata" if change.confidence == "inferred" else "not auto-fixable",
                )
            )
        return
    if not matched.locations:
        plan.skipped.append(Skipped(change.id, change.kind, None, "no affected code found"))
        return
    exact = [loc for loc in matched.locations if loc.path == old_leaf_path]
    if not exact:
        plan.skipped.append(
            Skipped(
                change.id,
                change.kind,
                None,
                "affected code reads a nested field of this rename; the enclosing key is migrated by its own finding",
            )
        )
        return
    for location in exact:
        if location.access_kind in ("subscript", "get_call"):
            plan.add(
                CodeEdit(
                    id=_edit_id(REPLACE_STRING, location.file, location.line, location.col, change.id),
                    kind=REPLACE_STRING,
                    file=location.file,
                    line=location.line,
                    col=location.col,
                    change_id=change.id,
                    change_kind=change.kind,
                    old_value=old_leaf,
                    new_value=new_leaf,
                    path=location.path,
                    note=f"rename `{old_leaf}` -> `{new_leaf}`",
                )
            )
        elif location.access_kind == "attribute":
            plan.add(
                CodeEdit(
                    id=_edit_id(REPLACE_ATTRIBUTE, location.file, location.line, location.col, change.id),
                    kind=REPLACE_ATTRIBUTE,
                    file=location.file,
                    line=location.line,
                    col=location.col,
                    change_id=change.id,
                    change_kind=change.kind,
                    old_value=old_leaf,
                    new_value=new_leaf,
                    path=location.path,
                    note=f"rename attribute `{old_leaf}` -> `{new_leaf}`",
                )
            )
        else:
            plan.skipped.append(
                Skipped(change.id, change.kind, location.as_dict(), f"unsupported access kind {location.access_kind}")
            )


def _plan_removal(matched: MatchedChange, plan: PatchPlan) -> None:
    change = matched.change
    default_expr = str(change.meta.get("default_expr") or "None")
    if not change.auto_fixable:
        for location in matched.locations or [None]:
            plan.skipped.append(
                Skipped(
                    change.id,
                    change.kind,
                    location.as_dict() if location else None,
                    "removal has no authorised strategy; add a `removals` rule or review manually",
                )
            )
        return
    if not matched.locations:
        plan.skipped.append(Skipped(change.id, change.kind, None, "no affected code found"))
        return
    exact = [loc for loc in matched.locations if loc.path == change.field_path]
    if not exact:
        plan.skipped.append(
            Skipped(change.id, change.kind, None, "only nested reads of this field were found; migrate the parent access")
        )
        return
    for location in exact:
        leaf = (change.field_path or "").rsplit(".", 1)[-1]
        if location.access_kind == "subscript":
            # The whole subscript expression is replaced, so target its position,
            # not the position of the key literal.
            line = location.expr_line or location.line
            col = location.expr_col or location.col
            plan.add(
                CodeEdit(
                    id=_edit_id(OPTIONAL_SUBSCRIPT, location.file, line, col, change.id),
                    kind=OPTIONAL_SUBSCRIPT,
                    file=location.file,
                    line=line,
                    col=col,
                    change_id=change.id,
                    change_kind=change.kind,
                    old_value=leaf,
                    value_expr=default_expr,
                    path=location.path,
                    note=f"`{leaf}` no longer exists; read it optionally",
                )
            )
        elif location.access_kind == "get_call":
            plan.skipped.append(
                Skipped(change.id, change.kind, location.as_dict(), "already uses an optional access; no edit required")
            )
        else:
            plan.skipped.append(
                Skipped(change.id, change.kind, location.as_dict(), "attribute access cannot be made optional safely")
            )


def _plan_required_field(matched: MatchedChange, plan: PatchPlan) -> None:
    change = matched.change
    value_expr = change.meta.get("value_expr")
    if not value_expr:
        for location in matched.locations or [None]:
            plan.skipped.append(
                Skipped(
                    change.id,
                    change.kind,
                    location.as_dict() if location else None,
                    "required field has no default value in migration metadata",
                )
            )
        return
    if not matched.locations:
        plan.skipped.append(Skipped(change.id, change.kind, None, "no affected call sites found"))
        return
    leaf = (change.field_path or "").rsplit(".", 1)[-1]
    for location in matched.locations:
        position = location.json_dict_pos
        if position is None:
            plan.skipped.append(
                Skipped(
                    change.id,
                    change.kind,
                    location.as_dict(),
                    "request body is not an inline dict literal; add the field manually",
                )
            )
            continue
        plan.add(
            CodeEdit(
                id=_edit_id(INSERT_DICT_KEY, location.file, position[0], position[1], change.id),
                kind=INSERT_DICT_KEY,
                file=location.file,
                line=position[0],
                col=position[1],
                change_id=change.id,
                change_kind=change.kind,
                new_value=leaf,
                value_expr=str(value_expr),
                path=change.field_path,
                note=f"add required request field `{leaf}`",
            )
        )


def _plan_path_change(matched: MatchedChange, plan: PatchPlan) -> None:
    change = matched.change
    old_endpoint = change.meta.get("old_endpoint") or change.endpoint_key or ""
    new_endpoint = change.meta.get("new_endpoint") or ""
    parts_old = old_endpoint.split(" ", 1)
    parts_new = new_endpoint.split(" ", 1)
    if len(parts_old) != 2 or len(parts_new) != 2:
        plan.skipped.append(Skipped(change.id, change.kind, None, "unparsable endpoint move"))
        return
    if not matched.locations:
        plan.skipped.append(Skipped(change.id, change.kind, None, "no affected call sites found"))
        return
    for location in matched.locations:
        literals = tuple(
            LiteralRef(line=int(item["line"]), col=int(item["col"]), value=str(item["value"]))
            for item in location.url_literals or []
        )
        if not literals:
            continue
        rewritten = rewrite_url_literal(literals, parts_old[1], parts_new[1])
        if rewritten is None:
            plan.skipped.append(
                Skipped(
                    change.id,
                    change.kind,
                    location.as_dict(),
                    "URL is assembled dynamically; rewrite the path manually",
                )
            )
            continue
        literal, new_value = rewritten
        plan.add(
            CodeEdit(
                id=_edit_id(REPLACE_STRING, location.file, literal.line, literal.col, change.id),
                kind=REPLACE_STRING,
                file=location.file,
                line=literal.line,
                col=literal.col,
                change_id=change.id,
                change_kind=change.kind,
                old_value=literal.value,
                new_value=new_value,
                path=change.path,
                note=f"{parts_old[1]} -> {parts_new[1]}",
            )
        )


def plan_patch(matched: list[MatchedChange]) -> PatchPlan:
    plan = PatchPlan()
    for item in matched:
        change: Change = item.change
        if change.kind in (RESPONSE_FIELD_RENAMED, "request_field_renamed"):
            _plan_rename(item, plan)
        elif change.kind == RESPONSE_FIELD_REMOVED or change.kind == "request_field_removed":
            _plan_removal(item, plan)
        elif change.kind == REQUEST_FIELD_REQUIRED_ADDED:
            _plan_required_field(item, plan)
        elif change.kind == PATH_CHANGED:
            _plan_path_change(item, plan)
        elif change.kind == ENDPOINT_REMOVED:
            for location in item.locations or [None]:
                plan.skipped.append(
                    Skipped(
                        change.id,
                        change.kind,
                        location.as_dict() if location else None,
                        "endpoint was removed; there is no mechanical replacement",
                    )
                )
        elif change.kind == PARAM_REQUIRED_ADDED:
            for location in item.locations or [None]:
                plan.skipped.append(
                    Skipped(
                        change.id,
                        change.kind,
                        location.as_dict() if location else None,
                        "a newly required parameter needs a value from you",
                    )
                )
        elif change.severity in (ADDITIVE, INFO):
            # A new field or enum value cannot break existing code, so there is
            # nothing to migrate and nothing to review.
            continue
        else:
            for location in item.locations or [None]:
                plan.skipped.append(
                    Skipped(
                        change.id,
                        change.kind,
                        location.as_dict() if location else None,
                        "change is reported for review, not auto-migrated",
                    )
                )
    return plan


def load_sources(root: Path, files: set[str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for rel in files:
        path = root / rel
        if path.is_file():
            sources[rel] = path.read_text(encoding="utf-8")
    return sources


def plan_to_json(plan: PatchPlan) -> str:
    return json.dumps([edit.as_dict() for edit in plan.edits], indent=2)
