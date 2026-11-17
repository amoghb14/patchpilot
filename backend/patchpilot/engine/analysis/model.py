"""Data structures produced by the Python code analyser."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LiteralRef:
    """A string literal (or f-string fragment) inside a URL expression."""

    line: int
    col: int  # 1-based, libcst compatible
    value: str


@dataclass(frozen=True)
class CallSite:
    file: str
    line: int
    col: int
    method: str
    path_template: str
    endpoint_key: str | None
    expression: str
    function: str
    keywords: tuple[str, ...] = ()
    json_keys: tuple[str, ...] | None = None
    json_dict_pos: tuple[int, int] | None = None
    url_literals: tuple[LiteralRef, ...] = ()
    http_lib: str = "requests"


@dataclass(frozen=True)
class FieldAccess:
    """A read of a response field on a value tainted by an API call."""

    file: str
    line: int
    col: int
    access_kind: str  # subscript | get_call | attribute
    field: str
    path: str  # dotted, array markers included: "data[].name"
    endpoints: tuple[str, ...]
    expression: str
    function: str
    expr_line: int = 0  # position of the whole expression, for whole-node rewrites
    expr_col: int = 0


@dataclass
class ScanResult:
    files_analyzed: int = 0
    syntax_errors: list[dict[str, str]] = field(default_factory=list)
    call_sites: list[CallSite] = field(default_factory=list)
    accesses: list[FieldAccess] = field(default_factory=list)
    unmapped_calls: list[CallSite] = field(default_factory=list)
    duration_ms: float = 0.0
    lines_scanned: int = 0

    def by_endpoint(self) -> dict[str, list[CallSite]]:
        out: dict[str, list[CallSite]] = {}
        for site in self.call_sites:
            if site.endpoint_key:
                out.setdefault(site.endpoint_key, []).append(site)
        return out
