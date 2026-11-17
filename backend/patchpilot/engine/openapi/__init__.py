"""OpenAPI normalization and semantic diff."""

from .differ import Change, diff_models, summarize
from .loader import SpecError, flatten_schema, normalize
from .metadata import MigrationMetadata
from .model import ApiModel, FieldSpec, Operation, ParamSpec

__all__ = [
    "ApiModel",
    "Change",
    "FieldSpec",
    "MigrationMetadata",
    "Operation",
    "ParamSpec",
    "SpecError",
    "diff_models",
    "flatten_schema",
    "normalize",
    "summarize",
]
