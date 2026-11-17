"""Correlate contract changes with concrete source locations.

An access is reported for a response change when the change's field path is a
*prefix* of the access path, so a rename of ``data`` also explains
``payload["data"][0]["name"]``. Edits are decided later by the planner, which
needs the access kind (subscript vs ``.get``) to pick a safe transformation.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from ..openapi.differ import (
    ENDPOINT_REMOVED,
    PARAM_REQUIRED_ADDED,
    PATH_CHANGED,
    REQUEST_FIELD_RENAMED,
    REQUEST_FIELD_REQUIRED_ADDED,
    RESPONSE_FIELD_RENAMED,
    Change,
)
from .model import CallSite, FieldAccess, ScanResult


@dataclass
class Location:
    file: str
    line: int
    col: int
    expression: str
    function: str
    access_kind: str | None = None
    field: str | None = None
    path: str | None = None
    access: FieldAccess | None = None
    site: CallSite | None = None
    expr_line: int = 0
    expr_col: int = 0
    url_literals: list[dict] = dc_field(default_factory=list)
    json_dict_pos: tuple[int, int] | None = None

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "expression": self.expression,
            "function": self.function,
            "access_kind": self.access_kind,
            "field": self.field,
            "path": self.path,
        }


@dataclass
class MatchedChange:
    change: Change
    locations: list[Location] = dc_field(default_factory=list)


def path_prefixes(path: str) -> list[str]:
    """``data[].name`` -> ``['data[]', 'data', 'data[].name', 'data[].name']``."""
    out: list[str] = []
    accumulated = ""
    for part in path.split("."):
        accumulated = f"{accumulated}.{part}" if accumulated else part
        out.append(accumulated)
        if part.endswith("[]"):
            out.append(accumulated[:-2])
    return list(dict.fromkeys(p for p in out if p))


def _site_matches(site: CallSite, change: Change) -> bool:
    if change.kind in (PATH_CHANGED, ENDPOINT_REMOVED):
        return True
    return True


def match_changes(scan: ScanResult, changes: list[Change]) -> list[MatchedChange]:
    sites_by_endpoint = scan.by_endpoint()
    matched: list[MatchedChange] = []

    for change in changes:
        endpoint = change.endpoint_key
        result = MatchedChange(change=change)
        if change.location == "response":
            # A rename is reported against the *new* path, but source code still
            # refers to the old one, so match on the recorded old path.
            target_path = change.field_path
            if change.kind in (RESPONSE_FIELD_RENAMED, REQUEST_FIELD_RENAMED):
                target_path = change.meta.get("old_path") or change.field_path
            if target_path:
                wanted = set(path_prefixes(target_path))
                for access in scan.accesses:
                    if endpoint not in access.endpoints:
                        continue
                    if access.path not in wanted:
                        continue
                    result.locations.append(
                        Location(
                            file=access.file,
                            line=access.line,
                            col=access.col,
                            expression=access.expression,
                            function=access.function,
                            access_kind=access.access_kind,
                            field=access.field,
                            path=access.path,
                            access=access,
                            expr_line=access.expr_line,
                            expr_col=access.expr_col,
                        )
                    )
        else:
            for site in sites_by_endpoint.get(endpoint or "", []):
                if not _site_matches(site, change):
                    continue
                if change.kind == REQUEST_FIELD_REQUIRED_ADDED and change.field_path:
                    leaf = change.field_path.rsplit(".", 1)[-1]
                    if site.json_keys and leaf in site.json_keys:
                        continue
                    if leaf in site.keywords:
                        continue
                if change.kind == PARAM_REQUIRED_ADDED and change.field_path:
                    if change.field_path in site.keywords:
                        continue
                result.locations.append(
                    Location(
                        file=site.file,
                        line=site.line,
                        col=site.col,
                        expression=site.expression,
                        function=site.function,
                        field=change.field_path,
                        site=site,
                        url_literals=[
                            {"line": literal.line, "col": literal.col, "value": literal.value}
                            for literal in site.url_literals
                        ],
                        json_dict_pos=site.json_dict_pos,
                    )
                )
        matched.append(result)

    matched.sort(key=lambda m: (m.change.severity, -len(m.locations), m.change.kind))
    return matched


def summarise(matched: list[MatchedChange]) -> dict:
    breaking = [m for m in matched if m.change.severity == "breaking"]
    with_locations = [m for m in matched if m.locations]
    return {
        "changes": len(matched),
        "breaking": len(breaking),
        "with_affected_code": len(with_locations),
        "affected_locations": sum(len(m.locations) for m in matched),
        "auto_fixable": sum(1 for m in matched if m.change.auto_fixable and m.locations),
        "needs_review": sum(1 for m in matched if m.change.requires_review),
    }
