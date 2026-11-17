"""Semantic OpenAPI diff.

The interesting part is *alignment*: before comparing fields we try to explain a
new field as a renamed old field. Renames cascade — if ``data`` becomes
``results`` and its element's ``name`` becomes ``full_name``, the second rename
must be discovered relative to the already-renamed parent. Everything else is
then compared field by field: types, enum values, requiredness.

Ambiguity is preserved, not hidden: an unauthorised rename hypothesis is emitted
as a breaking change with ``confidence="inferred"`` and ``requires_review=True``.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .metadata import MigrationMetadata, normalize_endpoint
from .model import ApiModel, FieldSpec, Operation

# --- change kinds -----------------------------------------------------------
ENDPOINT_REMOVED = "endpoint_removed"
ENDPOINT_ADDED = "endpoint_added"
PATH_CHANGED = "endpoint_path_changed"
RESPONSE_FIELD_REMOVED = "response_field_removed"
RESPONSE_FIELD_RENAMED = "response_field_renamed"
RESPONSE_FIELD_ADDED = "response_field_added"
RESPONSE_FIELD_TYPE_CHANGED = "response_field_type_changed"
RESPONSE_FIELD_NULLABLE_ADDED = "response_field_nullable_added"
RESPONSE_ENUM_VALUE_REMOVED = "response_enum_value_removed"
RESPONSE_ENUM_VALUE_ADDED = "response_enum_value_added"
RESPONSE_STATUS_REMOVED = "response_status_removed"
RESPONSE_STATUS_ADDED = "response_status_added"
REQUEST_FIELD_REMOVED = "request_field_removed"
REQUEST_FIELD_RENAMED = "request_field_renamed"
REQUEST_FIELD_ADDED = "request_field_added"
REQUEST_FIELD_REQUIRED_ADDED = "request_field_required_added"
REQUEST_FIELD_TYPE_CHANGED = "request_field_type_changed"
PARAM_REMOVED = "parameter_removed"
PARAM_ADDED = "parameter_added"
PARAM_REQUIRED_ADDED = "parameter_required_added"

BREAKING = "breaking"
WARNING = "warning"
ADDITIVE = "additive"
INFO = "info"


@dataclass(frozen=True)
class Change:
    id: str
    kind: str
    severity: str
    method: str | None
    path: str | None
    location: str  # endpoint | response | request_body | parameter
    field_path: str | None
    message: str
    old: Any = None
    new: Any = None
    auto_fixable: bool = False
    confidence: str = "n/a"  # explicit | inferred | n/a
    requires_review: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def endpoint_key(self) -> str | None:
        if self.method and self.path:
            return f"{self.method} {self.path}"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "method": self.method,
            "path": self.path,
            "endpoint": self.endpoint_key,
            "location": self.location,
            "field_path": self.field_path,
            "message": self.message,
            "old": self.old,
            "new": self.new,
            "auto_fixable": self.auto_fixable,
            "confidence": self.confidence,
            "requires_review": self.requires_review,
            "meta": self.meta,
        }


def _parent(path: str) -> str:
    return path.rsplit(".", 1)[0] if "." in path else ""


def _join(parent: str, leaf: str) -> str:
    return f"{parent}.{leaf}" if parent else leaf


def _depth(path: str) -> int:
    return path.count(".") + path.count("[]")


def _map_path(path: str, mapping: dict[str, str]) -> str:
    """Apply the deepest matching new->old prefix substitution."""
    best: tuple[str, str] | None = None
    for new_prefix, old_prefix in mapping.items():
        if path == new_prefix:
            continue
        if path.startswith(new_prefix + ".") or path.startswith(new_prefix + "["):
            if best is None or len(new_prefix) > len(best[0]):
                best = (new_prefix, old_prefix)
    if best is None:
        return path
    return best[1] + path[len(best[0]):]


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(name.lower()) if t}


def name_similarity(old_name: str, new_name: str) -> float:
    """Blend edit similarity with camel/snake token containment.

    ``name`` -> ``full_name`` has a mediocre character ratio (0.63) but is an
    obvious rename because every token of the old name is present in the new one.
    """
    ratio = difflib.SequenceMatcher(None, old_name.lower(), new_name.lower()).ratio()
    old_tokens, new_tokens = _tokens(old_name), _tokens(new_name)
    token_score = 0.0
    if old_tokens and new_tokens and (old_tokens <= new_tokens or new_tokens <= old_tokens):
        token_score = 0.86 + 0.1 * (min(len(old_tokens), len(new_tokens)) / max(len(old_tokens), len(new_tokens)))
    return max(ratio, token_score)


def _change_id(kind: str, method: str | None, path: str | None, location: str, field_path: str | None) -> str:
    raw = f"{kind}|{method}|{path}|{location}|{field_path or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _make(
    kind: str,
    severity: str,
    op: Operation | None,
    location: str,
    field_path: str | None,
    message: str,
    *,
    old: Any = None,
    new: Any = None,
    auto_fixable: bool = False,
    confidence: str = "n/a",
    requires_review: bool = False,
    method: str | None = None,
    path: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Change:
    method_value = method or (op.method if op else None)
    path_value = path if path is not None else (op.path if op else None)
    return Change(
        id=_change_id(kind, method_value, path_value, location, field_path),
        kind=kind,
        severity=severity,
        method=method_value,
        path=path_value,
        location=location,
        field_path=field_path,
        message=message,
        old=old,
        new=new,
        auto_fixable=auto_fixable,
        confidence=confidence,
        requires_review=requires_review,
        meta=meta or {},
    )


@dataclass
class Alignment:
    matched: list[tuple[str, str]] = field(default_factory=list)  # (old_path, new_path)
    renames: list[tuple[str, str, Any]] = field(default_factory=list)  # (old, new, rule)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def align_fields(
    old_fields: dict[str, FieldSpec],
    new_fields: dict[str, FieldSpec],
    metadata: MigrationMetadata,
    endpoint_key: str,
    location: str,
) -> Alignment:
    alignment = Alignment()
    mapping: dict[str, str] = {}
    claimed_old: set[str] = set()

    for new_path in sorted(new_fields, key=_depth):
        mapped = _map_path(new_path, mapping)
        if mapped in old_fields and mapped not in claimed_old:
            alignment.matched.append((mapped, new_path))
            claimed_old.add(mapped)
            continue

        leaf = new_fields[new_path].leaf
        parent_new = _parent(new_path)
        parent_old = _map_path(parent_new, mapping)
        rule = metadata.rename_rule(endpoint_key, location, leaf)
        if rule is not None:
            candidate = _join(parent_old, rule.from_field)
            if candidate in old_fields and candidate not in claimed_old:
                alignment.renames.append((candidate, new_path, rule))
                claimed_old.add(candidate)
                mapping[new_path] = candidate
                continue
        alignment.added.append(new_path)

    for old_path in old_fields:
        if old_path not in claimed_old:
            alignment.removed.append(old_path)
    return alignment


def _compare_matched(
    old_spec: FieldSpec,
    new_spec: FieldSpec,
    op: Operation,
    location: str,
    out: list[Change],
    *,
    status: str | None = None,
) -> None:
    prefix = "response" if location == "response" else "request"
    container = "response" if location == "response" else "request_body"
    if old_spec.type != new_spec.type:
        out.append(
            _make(
                f"{prefix}_field_type_changed",
                BREAKING,
                op,
                container,
                new_spec.path,
                f"`{new_spec.path}` changed type from `{old_spec.type}` to `{new_spec.type}`",
                old=old_spec.type,
                new=new_spec.type,
                auto_fixable=False,
                requires_review=True,
                meta={"status": status, "old_path": old_spec.path, "new_path": new_spec.path},
            )
        )
    old_enum = set(old_spec.enum or ())
    new_enum = set(new_spec.enum or ())
    if old_enum and new_enum:
        removed = sorted(old_enum - new_enum)
        if removed:
            out.append(
                _make(
                    f"{prefix}_enum_value_removed",
                    BREAKING,
                    op,
                    container,
                    new_spec.path,
                    f"`{new_spec.path}` no longer accepts: {', '.join(removed)}",
                    old=removed,
                    new=sorted(new_enum),
                    auto_fixable=False,
                    requires_review=True,
                    meta={"status": status, "removed_values": removed},
                )
            )
        added = sorted(new_enum - old_enum)
        if added:
            out.append(
                _make(
                    f"{prefix}_enum_value_added",
                    ADDITIVE,
                    op,
                    container,
                    new_spec.path,
                    f"`{new_spec.path}` gained enum values: {', '.join(added)}",
                    old=sorted(old_enum),
                    new=sorted(new_enum),
                    meta={"status": status},
                )
            )
    if new_spec.nullable and not old_spec.nullable:
        out.append(
            _make(
                f"{prefix}_field_nullable_added",
                WARNING,
                op,
                container,
                new_spec.path,
                f"`{new_spec.path}` can now be null",
                old=False,
                new=True,
                meta={"status": status},
            )
        )


def compare_fields(
    old_fields: dict[str, FieldSpec],
    new_fields: dict[str, FieldSpec],
    op: Operation,
    metadata: MigrationMetadata,
    location: str,
    out: list[Change],
    *,
    status: str | None = None,
    endpoint_key: str | None = None,
) -> None:
    endpoint = endpoint_key or op.key
    alignment = align_fields(old_fields, new_fields, metadata, endpoint, location)
    prefix = "response" if location == "response" else "request"
    container = "response" if location == "response" else "request_body"

    for old_path, new_path, rule in alignment.renames:
        out.append(
            _make(
                f"{prefix}_field_renamed",
                BREAKING,
                op,
                container,
                new_path,
                f"`{old_path}` was renamed to `{new_path}`",
                old=old_path,
                new=new_path,
                auto_fixable=True,
                confidence="explicit",
                requires_review=False,
                meta={
                    "status": status,
                    "old_path": old_path,
                    "new_path": new_path,
                    "old_leaf": old_fields[old_path].leaf,
                    "new_leaf": new_fields[new_path].leaf,
                },
            )
        )

    for old_path, new_path in alignment.matched:
        _compare_matched(old_fields[old_path], new_fields[new_path], op, location, out, status=status)
        if location == "request_body":
            old_spec, new_spec = old_fields[old_path], new_fields[new_path]
            if new_spec.required and not old_spec.required:
                rule = metadata.required_field_rule(endpoint, new_path)
                out.append(
                    _make(
                        REQUEST_FIELD_REQUIRED_ADDED,
                        BREAKING,
                        op,
                        "request_body",
                        new_path,
                        f"`{new_path}` is now required in the request body",
                        old=False,
                        new=True,
                        auto_fixable=bool(rule and rule.value_expr),
                        confidence="explicit" if rule and rule.value_expr else "n/a",
                        requires_review=not (rule and rule.value_expr),
                        meta={
                            "old_path": old_path,
                            "new_path": new_path,
                            "value_expr": (rule.value_expr if rule else None),
                        },
                    )
                )

    consumed: set[str] = set()
    for new_path in alignment.added:
        new_spec = new_fields[new_path]
        parent_new = _parent(new_path)
        parent_old = _map_path(parent_new, {new: old for old, new, _ in alignment.renames})
        hypothesis = None
        for old_path in alignment.removed:
            if old_path in consumed:
                continue
            old_spec = old_fields[old_path]
            if _parent(old_path) != parent_old or old_spec.type != new_spec.type:
                continue
            score = name_similarity(old_spec.leaf, new_spec.leaf)
            if score >= 0.8:
                hypothesis = (old_path, score)
                break
        if hypothesis is not None:
            old_path, score = hypothesis
            consumed.add(old_path)
            out.append(
                _make(
                    f"{prefix}_field_renamed",
                    BREAKING,
                    op,
                    container,
                    new_path,
                    f"`{old_path}` looks renamed to `{new_path}` (similarity {score:.2f}, no migration rule)",
                    old=old_path,
                    new=new_path,
                    auto_fixable=False,
                    confidence="inferred",
                    requires_review=True,
                    meta={
                        "status": status,
                        "old_path": old_path,
                        "new_path": new_path,
                        "similarity": round(score, 3),
                        "old_leaf": old_fields[old_path].leaf,
                        "new_leaf": new_spec.leaf,
                    },
                )
            )
            continue
        if location == "request_body" and new_spec.required:
            rule = metadata.required_field_rule(endpoint, new_path)
            out.append(
                _make(
                    REQUEST_FIELD_REQUIRED_ADDED,
                    BREAKING,
                    op,
                    "request_body",
                    new_path,
                    f"`{new_path}` is a new required request field",
                    old=None,
                    new=new_spec.type,
                    auto_fixable=bool(rule and rule.value_expr),
                    confidence="explicit" if rule and rule.value_expr else "n/a",
                    requires_review=not (rule and rule.value_expr),
                    meta={
                        "old_path": None,
                        "new_path": new_path,
                        "value_expr": (rule.value_expr if rule else None),
                    },
                )
            )
            continue
        out.append(
            _make(
                f"{prefix}_field_added",
                ADDITIVE,
                op,
                container,
                new_path,
                f"`{new_path}` was added",
                new=new_spec.type,
                meta={"status": status},
            )
        )

    for old_path in alignment.removed:
        if old_path in consumed:
            continue
        old_spec = old_fields[old_path]
        rule = metadata.removal_rule(endpoint, old_path) if location == "response" else None
        out.append(
            _make(
                f"{prefix}_field_removed",
                BREAKING,
                op,
                container,
                old_path,
                f"`{old_path}` ({old_spec.type}) was removed",
                old=old_spec.type,
                auto_fixable=bool(rule and rule.strategy == "optional_access"),
                confidence="explicit" if rule else "n/a",
                requires_review=not rule,
                meta={
                    "status": status,
                    "strategy": rule.strategy if rule else None,
                    "default_expr": rule.default_expr if rule else "None",
                },
            )
        )


def compare_operation(old_op: Operation, new_op: Operation, metadata: MigrationMetadata) -> list[Change]:
    out: list[Change] = []

    for status in sorted(set(old_op.responses) - set(new_op.responses)):
        out.append(
            _make(
                RESPONSE_STATUS_REMOVED,
                BREAKING,
                old_op,
                "response",
                None,
                f"response `{status}` was removed",
                old=status,
                auto_fixable=False,
                requires_review=True,
                meta={"status": status},
            )
        )
    for status in sorted(set(new_op.responses) - set(old_op.responses)):
        out.append(
            _make(
                RESPONSE_STATUS_ADDED,
                ADDITIVE,
                new_op,
                "response",
                None,
                f"response `{status}` was added",
                new=status,
                meta={"status": status},
            )
        )

    for status in sorted(set(old_op.responses) & set(new_op.responses)):
        old_fields = old_op.responses.get(status) or {}
        new_fields = new_op.responses.get(status) or {}
        if not old_fields and new_fields:
            continue
        compare_fields(old_fields, new_fields, old_op, metadata, "response", out, status=status)

    compare_fields(old_op.body, new_op.body, old_op, metadata, "request_body", out)

    for key in sorted(set(old_op.params) - set(new_op.params)):
        param = old_op.params[key]
        out.append(
            _make(
                PARAM_REMOVED,
                BREAKING,
                old_op,
                "parameter",
                param.name,
                f"{param.location} parameter `{param.name}` was removed",
                old=param.type,
                auto_fixable=False,
                requires_review=True,
                meta={"param_location": param.location},
            )
        )
    for key in sorted(set(new_op.params) - set(old_op.params)):
        param = new_op.params[key]
        out.append(
            _make(
                PARAM_ADDED,
                BREAKING if param.required else ADDITIVE,
                new_op,
                "parameter",
                param.name,
                f"{param.location} parameter `{param.name}` was added"
                + (" and is required" if param.required else ""),
                new=param.type,
                auto_fixable=not param.required,
                requires_review=param.required,
                meta={"param_location": param.location, "required": param.required},
            )
        )
    for key in sorted(set(old_op.params) & set(new_op.params)):
        old_param, new_param = old_op.params[key], new_op.params[key]
        if new_param.required and not old_param.required:
            out.append(
                _make(
                    PARAM_REQUIRED_ADDED,
                    BREAKING,
                    new_op,
                    "parameter",
                    new_param.name,
                    f"{new_param.location} parameter `{new_param.name}` is now required",
                    old=False,
                    new=True,
                    auto_fixable=False,
                    requires_review=True,
                    meta={"param_location": new_param.location, "required": True},
                )
            )
        if old_param.type != new_param.type:
            out.append(
                _make(
                    PARAM_REMOVED,
                    BREAKING,
                    new_op,
                    "parameter",
                    new_param.name,
                    f"{new_param.location} parameter `{new_param.name}` changed type "
                    f"`{old_param.type}` -> `{new_param.type}`",
                    old=old_param.type,
                    new=new_param.type,
                    auto_fixable=False,
                    requires_review=True,
                    meta={"param_location": new_param.location},
                )
            )
    return out


def diff_models(old: ApiModel, new: ApiModel, metadata: MigrationMetadata | None = None) -> list[Change]:
    metadata = metadata or MigrationMetadata()
    changes: list[Change] = []
    pairs: dict[str, str] = {}  # new_key -> old_key
    paired_old: set[str] = set()

    for rule in metadata.path_changes:
        old_key, new_key = normalize_endpoint(rule.from_endpoint), normalize_endpoint(rule.to_endpoint)
        if old_key in old.operations and new_key in new.operations and new_key not in pairs:
            pairs[new_key] = old_key
            paired_old.add(old_key)
            old_path = old.operations[old_key].path
            new_path = new.operations[new_key].path
            changes.append(
                _make(
                    PATH_CHANGED,
                    BREAKING,
                    None,
                    "endpoint",
                    None,
                    f"{old_key} moved to {new_key}",
                    old=old_key,
                    new=new_key,
                    auto_fixable=True,
                    confidence="explicit",
                    method=old.operations[old_key].method,
                    path=old_path,
                    meta={"old_endpoint": old_key, "new_endpoint": new_key, "new_path": new_path},
                )
            )

    for new_key in new.operations:
        if new_key in pairs:
            continue
        if new_key in old.operations:
            pairs[new_key] = new_key
            paired_old.add(new_key)

    for old_key in sorted(set(old.operations) - paired_old):
        op = old.operations[old_key]
        changes.append(
            _make(
                ENDPOINT_REMOVED,
                BREAKING,
                op,
                "endpoint",
                None,
                f"{old_key} was removed",
                old=old_key,
                auto_fixable=False,
                requires_review=True,
            )
        )
    for new_key in sorted(set(new.operations) - set(pairs)):
        op = new.operations[new_key]
        changes.append(
            _make(
                ENDPOINT_ADDED,
                ADDITIVE,
                op,
                "endpoint",
                None,
                f"{new_key} was added",
                new=new_key,
            )
        )

    for new_key, old_key in sorted(pairs.items()):
        old_op = old.operations[old_key]
        new_op = new.operations[new_key]
        for change in compare_operation(old_op, new_op, metadata):
            # Report against the path the application actually calls.
            changes.append(change)

    changes.sort(key=lambda c: (_severity_rank(c.severity), c.method or "", c.path or "", c.field_path or ""))
    return changes


def _severity_rank(severity: str) -> int:
    return {BREAKING: 0, WARNING: 1, ADDITIVE: 2, INFO: 3}.get(severity, 4)


def summarize(changes: list[Change]) -> dict[str, Any]:
    counts = {BREAKING: 0, WARNING: 0, ADDITIVE: 0, INFO: 0}
    for change in changes:
        counts[change.severity] = counts.get(change.severity, 0) + 1
    return {
        "total": len(changes),
        "by_severity": counts,
        "auto_fixable": sum(1 for c in changes if c.auto_fixable),
        "requires_review": sum(1 for c in changes if c.requires_review),
        "breaking_kinds": sorted({c.kind for c in changes if c.severity == BREAKING}),
    }
