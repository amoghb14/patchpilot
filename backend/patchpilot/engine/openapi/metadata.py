"""Explicit migration metadata supplied by the API provider (or by a human).

PatchPilot will *hypothesise* a rename on its own, but it only auto-applies one
when a rule here authorises it. That rule is the difference between a safe
migration engine and an LLM guessing field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def normalize_endpoint(value: str) -> str:
    """``"get  /v1/x"`` -> ``"GET /v1/x"``."""
    parts = value.strip().split()
    if len(parts) != 2:
        raise ValueError(f"endpoint must look like 'GET /v1/path', got {value!r}")
    method, path = parts
    return f"{method.upper()} {path}"


@dataclass(frozen=True)
class RenameRule:
    from_field: str
    to_field: str
    location: str = "response"
    endpoints: tuple[str, ...] = ()

    def applies_to(self, endpoint_key: str, location: str) -> bool:
        if self.location and self.location != location:
            return False
        if not self.endpoints:
            return True
        try:
            normalized = normalize_endpoint(endpoint_key)
        except ValueError:
            return False
        return normalized in {normalize_endpoint(e) for e in self.endpoints}


@dataclass(frozen=True)
class PathChangeRule:
    from_endpoint: str
    to_endpoint: str


@dataclass(frozen=True)
class RequiredFieldRule:
    endpoint: str
    field: str
    location: str = "request_body"
    value_expr: str | None = None


@dataclass(frozen=True)
class RemovalRule:
    endpoint: str | None
    field: str
    strategy: str = "optional_access"
    default_expr: str = "None"


@dataclass
class MigrationMetadata:
    renames: list[RenameRule] = field(default_factory=list)
    path_changes: list[PathChangeRule] = field(default_factory=list)
    required_fields: list[RequiredFieldRule] = field(default_factory=list)
    removals: list[RemovalRule] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MigrationMetadata":
        data = data or {}
        renames = [
            RenameRule(
                from_field=str(item["from"]),
                to_field=str(item["to"]),
                location=str(item.get("location") or "response"),
                endpoints=tuple(item.get("endpoints") or ()),
            )
            for item in data.get("renames") or []
            if "from" in item and "to" in item
        ]
        path_changes = [
            PathChangeRule(
                from_endpoint=normalize_endpoint(str(item["from"])),
                to_endpoint=normalize_endpoint(str(item["to"])),
            )
            for item in data.get("path_changes") or []
            if "from" in item and "to" in item
        ]
        required_fields = [
            RequiredFieldRule(
                endpoint=normalize_endpoint(str(item["endpoint"])),
                field=str(item["field"]),
                location=str(item.get("location") or "request_body"),
                value_expr=item.get("value_expr"),
            )
            for item in data.get("required_fields") or []
            if "endpoint" in item and "field" in item
        ]
        removals = [
            RemovalRule(
                endpoint=normalize_endpoint(str(item["endpoint"])) if item.get("endpoint") else None,
                field=str(item["field"]),
                strategy=str(item.get("strategy") or "optional_access"),
                default_expr=str(item.get("default_expr") or "None"),
            )
            for item in data.get("removals") or []
            if "field" in item
        ]
        return cls(
            renames=renames,
            path_changes=path_changes,
            required_fields=required_fields,
            removals=removals,
            notes=[str(n) for n in data.get("notes") or []],
        )

    def rename_rule(self, endpoint_key: str, location: str, new_leaf: str) -> RenameRule | None:
        for rule in self.renames:
            if rule.to_field == new_leaf and rule.applies_to(endpoint_key, location):
                return rule
        return None

    def required_field_rule(self, endpoint_key: str, field_path: str) -> RequiredFieldRule | None:
        for rule in self.required_fields:
            if rule.field in (field_path, field_path.rsplit(".", 1)[-1]):
                if normalize_endpoint(rule.endpoint) == normalize_endpoint(endpoint_key):
                    return rule
        return None

    def removal_rule(self, endpoint_key: str, field_path: str) -> RemovalRule | None:
        for rule in self.removals:
            if rule.field not in (field_path, field_path.rsplit(".", 1)[-1]):
                continue
            if rule.endpoint is None or normalize_endpoint(rule.endpoint) == normalize_endpoint(endpoint_key):
                return rule
        return None
