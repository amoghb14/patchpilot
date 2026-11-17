"""OpenAPI 3.x loading and normalization.

Responsibilities:
  * parse YAML or JSON from text / a file / a dict,
  * resolve internal ``$ref`` pointers (externally-hosted refs are out of scope),
  * merge ``allOf`` composition,
  * flatten object/array schemas into dotted paths such as ``data[].full_name``.

Everything here is pure functions over dicts so it can be unit tested without a
database or a web server.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable

import yaml

from .model import ApiModel, FieldSpec, Operation, ParamSpec

MAX_DEPTH = 8
MAX_FIELDS_PER_SCHEMA = 600
HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


class SpecError(ValueError):
    """Raised when a document cannot be interpreted as OpenAPI 3.x."""


class Resolver:
    """Resolves internal ``$ref`` pointers against a document."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, node: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(node, list):
            return [self.resolve(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#"):
                # External references cannot be fetched safely; keep them opaque.
                return {"type": "unknown", "x-patchpilot-external-ref": ref}
            if ref in seen:
                return {"type": "object", "x-patchpilot-cycle": ref}
            target = self._pointer(ref)
            merged = self.resolve(target, seen | {ref})
            extras = {k: v for k, v in node.items() if k not in ("$ref",)}
            if isinstance(merged, dict) and extras:
                merged = {**merged, **extras}
            return merged
        return {key: self.resolve(value, seen) for key, value in node.items()}

    def _pointer(self, ref: str) -> Any:
        node: Any = self.document
        for raw in ref.lstrip("#/").split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise SpecError(f"unresolvable $ref: {ref}")
            node = node[token]
        return node


def _merge_all_of(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``allOf`` into a single object schema (shallow but predictable)."""
    parts = schema.get("allOf")
    if not isinstance(parts, list):
        return schema
    merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for part in parts:
        if not isinstance(part, dict):
            continue
        sub = _merge_all_of(part)
        if sub.get("type") not in (None, "object"):
            merged["type"] = sub["type"]
        merged["properties"].update(sub.get("properties") or {})
        for name in sub.get("required") or []:
            if name not in merged["required"]:
                merged["required"].append(name)
    for key, value in schema.items():
        if key in ("allOf", "properties", "required"):
            continue
        merged[key] = value
    merged["properties"].update(schema.get("properties") or {})
    for name in schema.get("required") or []:
        if name not in merged["required"]:
            merged["required"].append(name)
    return merged


def _union_of(schema: dict[str, Any]) -> dict[str, Any] | None:
    for keyword in ("oneOf", "anyOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and branches:
            merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                if branch.get("type") not in (None, "object"):
                    merged["type"] = branch["type"]
                merged["properties"].update(branch.get("properties") or {})
            return merged
    return None


def flatten_schema(
    schema: Any,
    resolver: Resolver,
    prefix: str = "",
    required: bool = True,
    depth: int = 0,
    out: dict[str, FieldSpec] | None = None,
) -> dict[str, FieldSpec]:
    """Flatten a schema into ``{dotted.path: FieldSpec}``.

    Arrays are rendered with a ``[]`` suffix on the container path, so the element
    fields of ``{"data": {"type": "array", "items": Customer}}`` become
    ``data[].name``. Code analysis emits exactly the same shape for
    ``payload["data"][0]["name"]``.
    """
    if out is None:
        out = {}
    if depth > MAX_DEPTH or len(out) > MAX_FIELDS_PER_SCHEMA:
        return out
    if not isinstance(schema, dict):
        return out
    schema = resolver.resolve(schema)
    if not isinstance(schema, dict):
        return out
    schema = _merge_all_of(schema)
    union = _union_of(schema)
    if union is not None:
        schema = union
    if schema.get("x-patchpilot-cycle"):
        out.setdefault(
            prefix or "<root>",
            FieldSpec(prefix or "<root>", "object", required, depth=depth),
        )
        return out

    schema_type = schema.get("type")
    if schema_type is None:
        if "properties" in schema:
            schema_type = "object"
        elif "items" in schema:
            schema_type = "array"
        else:
            schema_type = "unknown"
    enumeration = schema.get("enum")
    enum_tuple = tuple(str(item) for item in enumeration) if isinstance(enumeration, list) else None
    nullable = bool(schema.get("nullable")) or "null" in (enum_tuple or ())

    if prefix:
        out[prefix] = FieldSpec(
            path=prefix,
            type=str(schema_type),
            required=required,
            enum=enum_tuple,
            nullable=nullable,
            description=schema.get("description"),
            depth=depth,
        )

    if schema_type == "object":
        properties = schema.get("properties") or {}
        required_names = set(schema.get("required") or [])
        for name, child in properties.items():
            child_path = f"{prefix}.{name}" if prefix else str(name)
            flatten_schema(
                child,
                resolver,
                prefix=child_path,
                required=name in required_names,
                depth=depth + 1,
                out=out,
            )
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict) and additional:
            child_path = f"{prefix}.*" if prefix else "*"
            flatten_schema(additional, resolver, prefix=child_path, required=False, depth=depth + 1, out=out)
    elif schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            flatten_schema(items, resolver, prefix=f"{prefix}[]" if prefix else "[]", required=True, depth=depth + 1, out=out)
    return out


def _loads(text: str) -> dict[str, Any]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    loaded = yaml.safe_load(text)
    if loaded is None:
        raise SpecError("document is empty")
    return loaded


def load_document(source: str | dict[str, Any] | os.PathLike[str]) -> tuple[dict[str, Any], str]:
    """Load a spec from a dict, a path, or a raw YAML/JSON string."""
    if isinstance(source, dict):
        payload = json.dumps(source, sort_keys=True, default=str)
        return source, payload
    if isinstance(source, os.PathLike):
        source = os.fspath(source)
    if isinstance(source, str) and os.path.exists(source) and "\n" not in source[:200]:
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = source
    document = _loads(text)
    if not isinstance(document, dict):
        raise SpecError("OpenAPI document must be an object at the root")
    return document, text


def normalize(source: str | dict[str, Any] | os.PathLike[str]) -> ApiModel:
    """Parse a document into an :class:`ApiModel`."""
    document, text = load_document(source)
    if "openapi" not in document and "swagger" not in document:
        raise SpecError("missing `openapi` version field; only OpenAPI 3.x is supported")
    version = str(document.get("openapi") or document.get("swagger") or "")
    if version.startswith("2"):
        raise SpecError("Swagger 2.0 is not supported; convert to OpenAPI 3.x first")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("document has no `paths` object")
    resolver = Resolver(document)
    operations: dict[str, Operation] = {}
    field_count = 0

    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method in HTTP_METHODS:
            raw_op = path_item.get(method)
            if not isinstance(raw_op, dict):
                continue
            op = Operation(
                method=method.upper(),
                path=str(raw_path),
                operation_id=raw_op.get("operationId"),
                summary=raw_op.get("summary"),
            )
            for param in list(shared_params) + list(raw_op.get("parameters") or []):
                resolved = resolver.resolve(param)
                if not isinstance(resolved, dict) or "name" not in resolved:
                    continue
                schema = resolver.resolve(resolved.get("schema") or {})
                enum = schema.get("enum") if isinstance(schema, dict) else None
                spec = ParamSpec(
                    name=str(resolved["name"]),
                    location=str(resolved.get("in") or "query"),
                    required=bool(resolved.get("required")),
                    type=str((schema or {}).get("type") or "unknown"),
                    enum=tuple(str(item) for item in enum) if isinstance(enum, list) else None,
                )
                op.params[spec.key] = spec
            body = raw_op.get("requestBody")
            if isinstance(body, dict):
                resolved_body = resolver.resolve(body)
                for media_type, media in (resolved_body.get("content") or {}).items():
                    if "json" not in media_type:
                        continue
                    op.body = flatten_schema(media.get("schema") or {}, resolver)
                    break
            for status, response in (raw_op.get("responses") or {}).items():
                resolved = resolver.resolve(response)
                content = (resolved or {}).get("content") or {}
                for media_type, media in content.items():
                    if "json" not in media_type:
                        continue
                    op.responses[str(status)] = flatten_schema(media.get("schema") or {}, resolver)
                    break
                if content:
                    pass
                op.responses.setdefault(str(status), {})
            field_count += len(op.body) + sum(len(f) for f in op.responses.values())
            operations[op.key] = op

    if not operations:
        raise SpecError("document declares no operations")
    info = document.get("info") or {}
    return ApiModel(
        title=str(info.get("title") or "untitled"),
        version=str(info.get("version") or ""),
        operations=operations,
        source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        operation_count=len(operations),
        field_count=field_count,
        openapi_version=version,
    )


def operation_keys(model: ApiModel) -> Iterable[str]:
    return model.operations.keys()
