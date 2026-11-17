"""Normalized, flattened representation of an OpenAPI document.

The rest of PatchPilot never walks raw OpenAPI dicts: it walks `Operation` objects
whose response and request schemas are flattened into dotted field paths
(``data[].full_name``). Flattening keeps the differ and the code analyser talking
about exactly the same vocabulary, which is what makes it possible to map a
response field change onto a ``source["field"]`` access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    """A single flattened field of an object/array schema."""

    path: str
    type: str
    required: bool
    enum: tuple[str, ...] | None = None
    nullable: bool = False
    description: str | None = None
    depth: int = 0

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    @property
    def parent(self) -> str:
        return self.path.rsplit(".", 1)[0] if "." in self.path else ""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    location: str  # path | query | header | cookie
    required: bool
    type: str
    enum: tuple[str, ...] | None = None

    @property
    def key(self) -> str:
        return f"{self.location}:{self.name}"


@dataclass
class Operation:
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    params: dict[str, ParamSpec] = field(default_factory=dict)
    body: dict[str, FieldSpec] = field(default_factory=dict)
    responses: dict[str, dict[str, FieldSpec]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class ApiModel:
    title: str
    version: str
    operations: dict[str, Operation]
    source_hash: str
    operation_count: int = 0
    field_count: int = 0
    openapi_version: str = "3.0.0"

    def endpoint_keys(self) -> list[str]:
        return sorted(self.operations)

    def operation(self, key: str) -> Operation | None:
        return self.operations.get(key)

    def all_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for op in self.operations.values():
            for status, fields in op.responses.items():
                for path, spec in fields.items():
                    out[f"{op.key}|response:{status}|{path}"] = spec
            for path, spec in op.body.items():
                out[f"{op.key}|request_body|{path}"] = spec
        return out
