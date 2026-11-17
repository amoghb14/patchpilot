"""Source-preserving code transformation with LibCST.

Every edit is addressed by ``(line, column)`` so unrelated formatting, comments
and string quoting survive untouched. The transformer only rewrites the exact
node it was told to, which is what makes the resulting diff reviewable: in the
demo migration the diff is a handful of one-line changes, not a reformatted file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from .planner import (
    INSERT_CALL_KWARG,
    INSERT_DICT_KEY,
    OPTIONAL_SUBSCRIPT,
    REPLACE_ATTRIBUTE,
    REPLACE_STRING,
    CodeEdit,
)


@dataclass
class AppliedEdit:
    edit: CodeEdit
    status: str  # applied | not_found | failed
    detail: str = ""


@dataclass
class TransformResult:
    file: str
    changed: bool
    new_source: str
    applied: list[AppliedEdit] = field(default_factory=list)
    failures: list[AppliedEdit] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return len(self.applied)


def _quote_like(template: str, value: str) -> str:
    for quote in ("'", '"'):
        if template.startswith(quote):
            escaped = value.replace("\\", "\\\\").replace(quote, "\\" + quote)
            return f"{quote}{escaped}{quote}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class PatchTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, edits: list[CodeEdit]):
        super().__init__()
        self.edits = edits
        self.exact = {(e.line, e.col): e for e in edits}
        self.by_value: dict[tuple[int, str], list[CodeEdit]] = {}
        self.used: set[str] = set()
        self.applied: list[AppliedEdit] = []
        self.failed: list[AppliedEdit] = []

    # -- helpers ---------------------------------------------------------
    def _match(self, node: cst.CSTNode, kinds: set[str], value: str | None = None) -> CodeEdit | None:
        position = self.get_metadata(PositionProvider, node)
        key = (position.start.line, position.start.column)
        candidate = self.exact.get(key)
        if candidate is not None and candidate.kind in kinds and candidate.id not in self.used:
            if value is None or candidate.old_value == value or candidate.kind != REPLACE_STRING:
                return candidate
        if value is not None:
            for edit in self.edits:
                if edit.id in self.used or edit.kind not in kinds:
                    continue
                if edit.line != position.start.line or edit.old_value != value:
                    continue
                self.used.add(edit.id)
                return edit
        return None

    def _mark(self, edit: CodeEdit, node: cst.CSTNode) -> None:
        self.used.add(edit.id)
        self.applied.append(AppliedEdit(edit=edit, status="applied"))

    # -- visitors --------------------------------------------------------
    def leave_SimpleString(self, original: cst.SimpleString, updated: cst.SimpleString) -> cst.SimpleString:
        value = original.evaluated_value
        if not isinstance(value, str):
            return updated
        edit = self._match(original, {REPLACE_STRING}, value)
        if edit is None or edit.new_value is None:
            return updated
        self._mark(edit, original)
        return updated.with_changes(value=_quote_like(original.value, edit.new_value))

    def leave_FormattedStringText(
        self, original: cst.FormattedStringText, updated: cst.FormattedStringText
    ) -> cst.FormattedStringText:
        edit = self._match(original, {REPLACE_STRING}, original.value)
        if edit is None or edit.new_value is None:
            return updated
        self._mark(edit, original)
        return updated.with_changes(value=edit.new_value)

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        edit = self._match(original, {REPLACE_ATTRIBUTE}, original.value)
        if edit is None or edit.new_value is None:
            return updated
        self._mark(edit, original)
        return updated.with_changes(value=edit.new_value)

    def leave_Subscript(self, original: cst.Subscript, updated: cst.Subscript) -> cst.CSTNode:
        edit = self._match(original, {OPTIONAL_SUBSCRIPT})
        if edit is None:
            return updated
        base_code = cst.Module([]).code_for_node(updated.value)
        raw_slice = updated.slice
        elements = raw_slice if isinstance(raw_slice, tuple) else (raw_slice,)
        if len(elements) != 1:
            self.failed.append(AppliedEdit(edit=edit, status="failed", detail="unsupported subscript shape"))
            return updated
        element = elements[0]
        key_node = None
        if isinstance(element, cst.SubscriptElement) and isinstance(element.slice, cst.Index):
            key_node = element.slice.value
        if key_node is None:
            self.failed.append(AppliedEdit(edit=edit, status="failed", detail="unsupported subscript shape"))
            return updated
        key_code = cst.Module([]).code_for_node(key_node)
        default = edit.value_expr or "None"
        try:
            replacement = cst.parse_expression(f"{base_code}.get({key_code}, {default})")
        except cst.ParserSyntaxError as exc:  # pragma: no cover - defensive
            self.failed.append(AppliedEdit(edit=edit, status="failed", detail=str(exc)))
            return updated
        self._mark(edit, original)
        lpar = getattr(original, "lpar", None)
        if lpar is not None:
            replacement = replacement.with_changes(lpar=lpar, rpar=getattr(original, "rpar", cst.MaybeSentinel.DEFAULT))
        return replacement

    def leave_Dict(self, original: cst.Dict, updated: cst.Dict) -> cst.Dict:
        edit = self._match(original, {INSERT_DICT_KEY})
        if edit is None or not edit.new_value:
            return updated
        key = cst.parse_expression(json.dumps(edit.new_value))
        value = cst.parse_expression(edit.value_expr or "None")
        element = cst.DictElement(key=key, value=value)
        elements = list(updated.elements)
        if elements and isinstance(elements[-1], cst.DictElement) and elements[-1].comma is cst.MaybeSentinel.DEFAULT:
            # Keep the one-line dict readable: `{"a": b, "c": d}`.
            elements[-1] = elements[-1].with_changes(
                comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
            )
        self._mark(edit, original)
        return updated.with_changes(elements=[*elements, element])

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.Call:
        edit = self._match(original, {INSERT_CALL_KWARG})
        if edit is None or not edit.new_value:
            return updated
        keyword = cst.Arg(
            keyword=cst.Name(edit.new_value),
            value=cst.parse_expression(edit.value_expr or "None"),
            equal=cst.AssignEqual(),
        )
        self._mark(edit, original)
        return updated.with_changes(keywords=[*updated.keywords, keyword])


def apply_edits(source: str, edits: list[CodeEdit], filename: str = "<source>") -> TransformResult:
    """Apply edits to one file's source and validate the result still compiles."""
    module = cst.parse_module(source)
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    transformer = PatchTransformer(edits)
    try:
        new_module = wrapper.visit(transformer)
    except Exception as exc:  # pragma: no cover - libcst internal failure
        return TransformResult(
            file=filename,
            changed=False,
            new_source=source,
            failures=[AppliedEdit(edit=e, status="failed", detail=f"{type(exc).__name__}: {exc}") for e in edits],
        )
    new_source = new_module.code
    if new_source != source:
        try:
            compile(new_source, filename, "exec")
        except SyntaxError as exc:
            return TransformResult(
                file=filename,
                changed=False,
                new_source=source,
                failures=[
                    AppliedEdit(edit=e, status="failed", detail=f"result does not compile: {exc}") for e in edits
                ],
            )
    applied_ids = {a.edit.id for a in transformer.applied}
    missing = [AppliedEdit(edit=e, status="not_found", detail="no matching CST node") for e in edits if e.id not in applied_ids]
    return TransformResult(
        file=filename,
        changed=new_source != source,
        new_source=new_source,
        applied=transformer.applied,
        failures=transformer.failed + missing,
    )


def apply_plan_patch(sources: dict[str, str], edits_by_file: dict[str, list[CodeEdit]]) -> dict[str, TransformResult]:
    results: dict[str, TransformResult] = {}
    for filename, edits in edits_by_file.items():
        source = sources.get(filename)
        if source is None:
            continue
        results[filename] = apply_edits(source, edits, filename)
    return results
