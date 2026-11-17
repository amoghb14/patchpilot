"""Taint-style static analysis for Python clients of an HTTP API.

The problem this solves: given ``customer["name"]`` somewhere in a repository,
decide whether *that* ``customer`` is a response body from ``GET /v1/customers``
or an unrelated local dictionary. Doing this by grepping for string literals
produces garbage: the demo repo contains a CSV importer that also has
``customer["name"]``.

Approach: a bounded, intra-procedural taint analysis with two small
inter-procedural extensions.

  1. Outbound HTTP calls are identified structurally (``requests.get``,
     ``session.post``, ``httpx.Client.get``, ...) and their URL is parsed into a
     path template that is matched against the OpenAPI operations.
  2. Taint flows through assignments, ``.json()``, subscripts (including
     ``payload["data"][0]["name"]`` -> ``data[].name``), loops and
     comprehensions. Reading a field on a tainted value is recorded with its
     exact path and source position.
  3. *Return summaries*: a wrapper such as ``get_customer() -> resp.json()``
     returns taint, so fields read in its callers are attributable too.
  4. *Parameter injection*: when a tainted value is passed into a local function,
     that function is re-analysed with the parameter tainted (bounded to a few
     rounds), so ``summarise(customer)`` is covered as well.

Anything the analysis cannot prove simply produces no finding, which keeps the
false-positive rate low at the cost of recall. Unmapped calls are reported so a
human can see what the tool ignored.
"""

from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..openapi.model import ApiModel
from .model import CallSite, FieldAccess, LiteralRef, ScanResult

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
HTTP_LIB_ROOTS = {"requests", "httpx", "aiohttp", "urllib3"}
CLIENT_NAME_HINTS = ("session", "client", "http", "api", "conn", "sess", "transport")
NON_FIELD_ATTRS = {
    "json",
    "text",
    "content",
    "headers",
    "status_code",
    "ok",
    "reason",
    "url",
    "raise_for_status",
    "close",
    "get",
    "keys",
    "values",
    "items",
    "copy",
    "pop",
    "update",
    "setdefault",
    "append",
    "extend",
    "format",
    "strip",
    "lower",
    "upper",
    "split",
    "encode",
    "decode",
}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "site-packages",
}
MAX_FILES = 2000
MAX_FILE_BYTES = 1_000_000
MAX_ROUNDS = 4
MAX_INJECTIONS_PER_FUNCTION = 40


@dataclass(frozen=True)
class Taint:
    endpoint: str
    path: tuple[str, ...] = ()

    def child(self, segment: str) -> "Taint":
        return Taint(self.endpoint, self.path + (segment,))

    @property
    def path_str(self) -> str:
        out = ""
        for segment in self.path:
            if segment == "[]":
                out += "[]"
            elif not out:
                out = segment
            else:
                out += "." + segment
        return out


Taints = frozenset[Taint]


def _elem(taints: Taints) -> Taints:
    return frozenset(Taint(t.endpoint, t.path + ("[]",)) for t in taints)


def _pos(node: ast.AST) -> tuple[int, int]:
    """Line (1-based) and column (0-based) - the same convention LibCST uses."""
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _segment_split(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def path_matches(code_path: str, spec_path: str) -> bool:
    """Compare a URL path template from code with one from the spec."""
    code_parts = _segment_split(code_path)
    spec_parts = _segment_split(spec_path)
    if len(code_parts) != len(spec_parts):
        return False
    for code, spec in zip(code_parts, spec_parts):
        if code.startswith("{") or spec.startswith("{"):
            continue
        if code != spec:
            return False
    return True


class UrlParse:
    """Result of statically evaluating a URL expression."""

    __slots__ = ("path", "method", "literals")

    def __init__(self, path: str, literals: tuple[LiteralRef, ...], method: str):
        self.path = path
        self.literals = literals
        self.method = method


class ModuleInfo:
    def __init__(self, relpath: str, module_name: str):
        self.relpath = relpath
        self.module_name = module_name
        self.consts: dict[str, ast.AST] = {}
        self.import_aliases: dict[str, str] = {}
        self.imported_names: dict[str, tuple[str, str]] = {}
        self.functions: dict[str, ast.AST] = {}


def iter_functions(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Yield ``(qualname, node)`` for every function, including methods."""
    found: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                found.append((name, child))
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return found


def module_name_for(relpath: str) -> str:
    parts = relpath[:-3].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class RepoScanner:
    def __init__(self, root: Path, model: ApiModel, extra_roots: Iterable[Path] = ()):
        self.root = Path(root)
        self.model = model
        self.extra_roots = [Path(p) for p in extra_roots]
        self.modules: dict[str, ModuleInfo] = {}
        self.by_module: dict[str, ModuleInfo] = {}
        self.by_name: dict[str, list[str]] = {}
        self.by_method: dict[str, list[str]] = {}
        self.function_nodes: dict[str, ast.AST] = {}
        self.summaries: dict[str, Taints] = {}
        self.param_env: dict[tuple[str, str], Taints] = {}
        self.call_sites: list[CallSite] = []
        self.accesses: list[FieldAccess] = []
        self.unmapped: list[CallSite] = []
        self.syntax_errors: list[dict[str, str]] = []
        self._lines = 0
        self._access_seen: set[tuple] = set()
        self._call_seen: set[tuple] = set()
        self._unmapped_seen: set[tuple] = set()

    # -- discovery ---------------------------------------------------------
    def discover(self) -> list[Path]:
        files: list[Path] = []
        for base in [self.root, *self.extra_roots]:
            if not base.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
                for filename in sorted(filenames):
                    if not filename.endswith(".py"):
                        continue
                    path = Path(dirpath) / filename
                    try:
                        if path.stat().st_size > MAX_FILE_BYTES:
                            continue
                    except OSError:
                        continue
                    files.append(path)
                    if len(files) >= MAX_FILES:
                        return files
        return files

    def _parse(self, path: Path) -> tuple[str, ast.Module] | None:
        rel = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else path.as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.syntax_errors.append({"file": rel, "error": f"unreadable: {exc}"})
            return None
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            self.syntax_errors.append({"file": rel, "error": f"SyntaxError line {exc.lineno}: {exc.msg}"})
            return None
        self._lines += source.count("\n") + 1
        info = ModuleInfo(rel, module_name_for(rel))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    info.import_aliases[local] = alias.name if alias.asname else alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    info.imported_names[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if value is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            info.consts[target.id] = value
        for qualname, node in iter_functions(tree):
            info.functions[qualname] = node
            func_id = f"{rel}::{qualname}"
            self.function_nodes[func_id] = node
            short = qualname.rsplit(".", 1)[-1]
            self.by_name.setdefault(short, []).append(func_id)
            if "." in qualname:
                self.by_method.setdefault(short, []).append(func_id)
        self.modules[rel] = info
        if info.module_name:
            self.by_module[info.module_name] = info
        return rel, tree

    # -- function resolution ----------------------------------------------
    def _lookup_in_module(self, module_name: str | None, name: str) -> tuple[str, ast.AST] | None:
        target = self.by_module.get(module_name or "")
        if target is None:
            return None
        for qualname, node in target.functions.items():
            if qualname.rsplit(".", 1)[-1] == name:
                return (f"{target.relpath}::{qualname}", node)
        return None

    def resolve_function(self, call: ast.Call, rel: str) -> tuple[str, ast.AST] | None:
        """Resolve a call to a function or method defined inside the repository.

        Used for return summaries (a wrapper that returns parsed JSON) and for
        parameter injection (a helper that consumes a response body). Instance
        methods are resolved by name across the repo, preferring the same module;
        an ambiguous name is skipped rather than guessed.
        """
        info = self.modules.get(rel)
        if info is None:
            return None
        func = call.func
        if isinstance(func, ast.Name):
            local = self._lookup_in_module(info.module_name, func.id)
            if local:
                return local
            imported = info.imported_names.get(func.id)
            if imported:
                found = self._lookup_in_module(imported[0], imported[1])
                if found:
                    return found
            return self._pick(func.id, rel)
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                module = info.import_aliases.get(func.value.id)
                if module is None and func.value.id in info.imported_names:
                    module = info.imported_names[func.value.id][0]
                found = self._lookup_in_module(module, func.attr)
                if found:
                    return found
            return self._pick(func.attr, rel)
        return None

    def _pick(self, name: str, rel: str) -> tuple[str, ast.AST] | None:
        candidates = self.by_method.get(name) or self.by_name.get(name) or []
        if not candidates:
            return None
        same_module = [c for c in candidates if c.startswith(f"{rel}::")]
        chosen = same_module[0] if len(same_module) == 1 else (candidates[0] if len(candidates) == 1 else None)
        if chosen is None:
            return None
        node = self.function_nodes.get(chosen)
        if node is None:
            return None
        return (chosen, node)

    # -- main ---------------------------------------------------------------
    def scan(self) -> ScanResult:
        started = time.perf_counter()
        paths = self.discover()
        trees: dict[str, ast.Module] = {}
        for path in paths:
            parsed = self._parse(path)
            if parsed:
                trees[parsed[0]] = parsed[1]

        for _round in range(MAX_ROUNDS):
            changed = False
            for rel, tree in trees.items():
                for label, node in self.modules[rel].functions.items():
                    analyzer = FunctionAnalyzer(self, rel, label, node, self._module_env(rel))
                    analyzer.run()
                    key = f"{rel}::{label}"
                    merged = self.summaries.get(key, frozenset()) | analyzer.return_taints
                    if merged != self.summaries.get(key):
                        self.summaries[key] = merged
                        changed = True
                    changed |= self._merge_injections(key, analyzer.injections)
            if not changed:
                break

        self.call_sites.clear()
        self.accesses.clear()
        self.unmapped.clear()
        for rel, tree in trees.items():
            analyzer = FunctionAnalyzer(self, rel, "<module>", None, self._module_env(rel), record=True)
            analyzer.run()
            for label, node in self.modules[rel].functions.items():
                func_id = f"{rel}::{label}"
                param_envs = {
                    param: taints
                    for (owner, param), taints in self.param_env.items()
                    if owner == func_id
                }
                analyzer = FunctionAnalyzer(
                    self, rel, label, node, self._module_env(rel), record=True, param_envs=param_envs,
                )
                analyzer.run()

        return ScanResult(
            files_analyzed=len(trees),
            syntax_errors=self.syntax_errors,
            call_sites=sorted(self.call_sites, key=lambda c: (c.file, c.line)),
            accesses=sorted(self.accesses, key=lambda a: (a.file, a.line, a.col)),
            unmapped_calls=sorted(self.unmapped, key=lambda c: (c.file, c.line)),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            lines_scanned=self._lines,
        )

    def _module_env(self, rel: str) -> dict[str, Taints]:
        return {}

    def _merge_injections(self, key: str, injections: list[tuple[str, str, Taints]]) -> bool:
        changed = False
        per_function: dict[str, int] = {}
        for func_id, param, taints in injections:
            if not func_id or not param:
                continue
            per_function[func_id] = per_function.get(func_id, 0) + 1
            if per_function[func_id] > MAX_INJECTIONS_PER_FUNCTION:
                continue
            slot = (func_id, param)
            merged = self.param_env.get(slot, frozenset()) | taints
            if merged != self.param_env.get(slot):
                self.param_env[slot] = merged
                changed = True
        return changed

    # -- recording ---------------------------------------------------------
    def record_call(self, site: CallSite) -> None:
        key = (site.file, site.line, site.col, site.method, site.path_template)
        if key in self._call_seen:
            return
        self._call_seen.add(key)
        if site.endpoint_key:
            self.call_sites.append(site)
        else:
            self.unmapped.append(site)

    def record_access(self, access: FieldAccess) -> None:
        key = (access.file, access.line, access.col, access.access_kind, access.field, access.endpoints, access.path)
        if key in self._access_seen:
            return
        self._access_seen.add(key)
        self.accesses.append(access)


def _client_like(expr: ast.AST) -> bool:
    if isinstance(expr, ast.Name):
        name = expr.id.lower()
        return name in {"s", "c"} or any(hint in name for hint in CLIENT_NAME_HINTS)
    if isinstance(expr, ast.Attribute):
        return _client_like(expr.value) or any(hint in expr.attr.lower() for hint in CLIENT_NAME_HINTS)
    return False


def _call_root_name(expr: ast.AST) -> str | None:
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    if isinstance(expr, ast.Name):
        return expr.id
    return None


class FunctionAnalyzer:
    def __init__(
        self,
        scanner: RepoScanner,
        rel: str,
        label: str,
        node: ast.AST | None,
        module_env: dict[str, Taints],
        *,
        record: bool = False,
        param_envs: dict[str, Taints] | None = None,
    ):
        self.scanner = scanner
        self.rel = rel
        self.label = label
        self.node = node
        self.env: dict[str, Taints] = dict(module_env)
        self.record = record
        self.param_envs = param_envs or {}
        self.return_taints: Taints = frozenset()
        self.injections: list[tuple[str, str, Taints]] = []
        self.module = scanner.modules.get(rel)
        self._depth = 0

    # -- entry --------------------------------------------------------------
    def run(self) -> None:
        if self.node is None:
            return
        args = getattr(self.node, "args", None)
        if args is not None and self.param_envs:
            positional = list(getattr(args, "posonlyargs", [])) + list(args.args)
            for arg in positional:
                if arg.arg in self.param_envs:
                    self.env[arg.arg] = self.param_envs[arg.arg]
            for extra in (args.vararg, args.kwarg):
                if extra is not None and extra.arg in self.param_envs:
                    self.env[extra.arg] = self.param_envs[extra.arg]
        body = getattr(self.node, "body", [])
        self.block(body)
        self.block(body)

    # -- statements ---------------------------------------------------------
    def block(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            self.stmt(stmt)

    def stmt(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return  # analysed separately by the repo scanner
        if isinstance(node, ast.Assign):
            taints = self.eval(node.value)
            for target in node.targets:
                self.bind(target, taints)
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.bind(node.target, self.eval(node.value))
        elif isinstance(node, ast.AugAssign):
            self.eval(node.value)
        elif isinstance(node, ast.Expr):
            self.eval(node.value)
        elif isinstance(node, ast.Return):
            if node.value is not None:
                self.return_taints = self.return_taints | self.eval(node.value)
        elif isinstance(node, ast.If):
            self.eval(node.test)
            self._branch(node.body, node.orelse)
        elif isinstance(node, ast.For) or isinstance(node, ast.AsyncFor):
            iter_taints = self.eval(node.iter)
            self.bind(node.target, _elem(iter_taints))
            self.block(node.body)
            self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, ast.While):
            self.eval(node.test)
            self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, ast.With) or isinstance(node, ast.AsyncWith):
            for item in node.items:
                taints = self.eval(item.context_expr)
                if item.optional_vars is not None:
                    self.bind(item.optional_vars, taints)
            self.block(node.body)
        elif isinstance(node, ast.Try):
            self.block(node.body)
            for handler in node.handlers:
                if handler.type is not None:
                    self.eval(handler.type)
                self.block(handler.body)
            self.block(node.orelse)
            self.block(node.finalbody)
        elif isinstance(node, ast.Match):
            self.eval(node.subject)
            for case in node.cases:
                self.block(case.body)
        elif isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass, ast.Global, ast.Nonlocal, ast.Break, ast.Continue)):
            return
        elif isinstance(node, ast.Raise):
            if node.exc is not None:
                self.eval(node.exc)
        elif isinstance(node, ast.Assert):
            self.eval(node.test)

    def _branch(self, body: list[ast.stmt], orelse: list[ast.stmt]) -> None:
        before = dict(self.env)
        self.block(body)
        after_body = dict(self.env)
        self.env = dict(before)
        self.block(orelse)
        after_else = dict(self.env)
        merged = dict(after_body)
        for key, value in after_else.items():
            merged[key] = merged.get(key, frozenset()) | value
        self.env = merged

    def bind(self, target: ast.AST, taints: Taints) -> None:
        key = self.expr_key(target)
        if key:
            existing = self.env.get(key, frozenset())
            self.env[key] = existing | taints
            return
        if isinstance(target, (ast.Tuple, ast.List)) and taints:
            element = _elem(taints)
            for child in target.elts:
                self.bind(child, element)
            return
        if isinstance(target, ast.Starred):
            self.bind(target.value, _elem(taints))

    @staticmethod
    def expr_key(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = FunctionAnalyzer.expr_key(node.value)
            if base:
                return f"{base}.{node.attr}"
        if isinstance(node, ast.Subscript):
            base = FunctionAnalyzer.expr_key(node.value)
            key = node.slice
            if base and isinstance(key, ast.Constant) and isinstance(key.value, str):
                return f"{base}[{key.value!r}]"
        return None

    # -- expressions --------------------------------------------------------
    def eval(self, node: ast.AST | None) -> Taints:
        if node is None or self._depth > 40:
            return frozenset()
        self._depth += 1
        try:
            return self._eval(node)
        finally:
            self._depth -= 1

    def _eval(self, node: ast.AST) -> Taints:
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node)
        if isinstance(node, ast.Attribute):
            return self._eval_attribute(node)
        if isinstance(node, ast.Name):
            return self.env.get(node.id, frozenset())
        if isinstance(node, ast.Await):
            return self.eval(node.value)
        if isinstance(node, ast.IfExp):
            self.eval(node.test)
            return self.eval(node.body) | self.eval(node.orelse)
        if isinstance(node, ast.NamedExpr):
            taints = self.eval(node.value)
            self.bind(node.target, taints)
            return taints
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            saved = dict(self.env)
            self._bind_generators(node.generators)
            result = self.eval(node.elt)
            self.env = saved
            return result
        if isinstance(node, ast.DictComp):
            saved = dict(self.env)
            self._bind_generators(node.generators)
            self.eval(node.key)
            result = self.eval(node.value)
            self.env = saved
            return result
        if isinstance(node, ast.BoolOp):
            out: Taints = frozenset()
            for value in node.values:
                out |= self.eval(value)
            return out
        if isinstance(node, ast.Starred):
            return self.eval(node.value)
        if isinstance(node, ast.Dict):
            out = frozenset()
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    out |= self.eval(key)
                out |= self.eval(value)
            return out
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            out = frozenset()
            for element in node.elts:
                out |= self.eval(element)
            return out
        if isinstance(node, ast.JoinedStr):
            out = frozenset()
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    out |= self.eval(value.value)
            return out
        if isinstance(node, ast.Compare):
            out = self.eval(node.left)
            for comparator in node.comparators:
                out |= self.eval(comparator)
            return out
        if isinstance(node, ast.BinOp):
            return self.eval(node.left) | self.eval(node.right)
        if isinstance(node, ast.UnaryOp):
            return self.eval(node.operand)
        if isinstance(node, ast.Slice):
            out = frozenset()
            for part in (node.lower, node.upper, node.step):
                out |= self.eval(part)
            return out
        if isinstance(node, ast.Lambda):
            return frozenset()
        # Conservative fallback: still walk children so accesses inside an
        # expression shape we do not model are recorded rather than silently lost.
        out = frozenset()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                out |= self.eval(child)
        return out

    def _bind_generators(self, generators: list[ast.comprehension]) -> None:
        for gen in generators:
            iter_taints = self.eval(gen.iter)
            self.bind(gen.target, _elem(iter_taints))
            for cond in gen.ifs:
                self.eval(cond)

    def _eval_subscript(self, node: ast.Subscript) -> Taints:
        base = self.eval(node.value)
        if not base:
            return frozenset()
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            line, col = _pos(key)
            self._record_access(node, key.value, key.value, "subscript", base, line, col)
            return frozenset(t.child(key.value) for t in base)
        if isinstance(key, ast.Slice):
            return base
        return _elem(base)

    def _eval_attribute(self, node: ast.Attribute) -> Taints:
        base = self.eval(node.value)
        if not base or node.attr in NON_FIELD_ATTRS:
            return frozenset()
        # Point at the attribute identifier itself, not the start of the receiver.
        line = getattr(node, "end_lineno", node.lineno)
        end_col = getattr(node, "end_col_offset", node.col_offset + len(node.attr))
        col = end_col - len(node.attr)
        self._record_access(node, node.attr, node.attr, "attribute", base, line, col)
        return frozenset(t.child(node.attr) for t in base)

    def _record_access(
        self,
        node: ast.AST,
        field: str,
        _key: str,
        kind: str,
        base: Taints,
        line: int,
        col: int,
    ) -> None:
        if not self.record:
            return
        endpoints = tuple(sorted({t.endpoint for t in base}))
        paths = sorted({t.path_str for t in base})
        expression = ast.unparse(node) if hasattr(ast, "unparse") else field
        if len(expression) > 160:
            expression = expression[:157] + "..."
        expr_line, expr_col = _pos(node)
        for path in paths:
            full = f"{path}.{field}" if path else field
            self.scanner.record_access(
                FieldAccess(
                    file=self.rel,
                    line=line,
                    col=col,
                    access_kind=kind,
                    field=field,
                    path=full,
                    endpoints=endpoints,
                    expression=expression,
                    function=self.label,
                    expr_line=expr_line,
                    expr_col=expr_col,
                )
            )

    def _eval_call(self, node: ast.Call) -> Taints:
        http = self._http_call(node)
        if http is not None:
            return http
        func = node.func
        arg_taints = [self.eval(arg) for arg in node.args]
        for keyword in node.keywords:
            self.eval(keyword.value)

        if isinstance(func, ast.Attribute):
            attr = func.attr
            base = self.eval(func.value)
            if attr == "json":
                return base
            if attr in {"get", "pop", "setdefault"}:
                if base and node.args:
                    key_node = node.args[0]
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                        line, col = _pos(key_node)
                        self._record_access(node, key_node.value, key_node.value, "get_call", base, line, col)
                        return frozenset(t.child(key_node.value) for t in base)
                return base
            resolved = self.scanner.resolve_function(node, self.rel)
            if resolved:
                return self._apply_call(resolved, node, arg_taints, positional_offset=0)
            return base
        if isinstance(func, ast.Name):
            resolved = self.scanner.resolve_function(node, self.rel)
            if resolved:
                return self._apply_call(resolved, node, arg_taints, positional_offset=0)
        return frozenset()

    def _apply_call(
        self, resolved: tuple[str, ast.AST], node: ast.Call, arg_taints: list[Taints], positional_offset: int
    ) -> Taints:
        func_id, definition = resolved
        args = getattr(definition, "args", None)
        params: list[str] = []
        if args is not None:
            params = [a.arg for a in list(getattr(args, "posonlyargs", [])) + list(args.args)]
        for index, taints in enumerate(arg_taints):
            param_index = index + positional_offset
            if param_index < len(params) and taints:
                self.injections.append((func_id, params[param_index], taints))
        for keyword in node.keywords:
            if keyword.arg and keyword.arg in params:
                taints = self.eval(keyword.value)
                if taints:
                    self.injections.append((func_id, keyword.arg, taints))
        return self.scanner.summaries.get(func_id, frozenset())

    # -- HTTP call detection ------------------------------------------------
    def _http_call(self, node: ast.Call) -> Taints | None:
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
            return None
        root = _call_root_name(func)
        if root is None:
            return None
        is_lib = root in HTTP_LIB_ROOTS
        is_client = _client_like(func.value)
        if not (is_lib or is_client):
            return None
        if is_client and not is_lib and self._is_local_client_wrapper(func):
            return None

        url_expr: ast.AST | None = None
        if node.args:
            url_expr = node.args[0]
        else:
            for keyword in node.keywords:
                if keyword.arg in ("url", "uri"):
                    url_expr = keyword.value
                    break
        if url_expr is None:
            return frozenset()

        parsed = self._parse_url(url_expr, func.attr)
        if parsed is None:
            self._record_unmapped(node, func.attr, url_expr)
            return frozenset()
        endpoints = self._match_endpoints(parsed.method, parsed.path)
        if not endpoints:
            self._record_unmapped(node, func.attr, url_expr, path=parsed.path)
            return frozenset()
        for endpoint in endpoints:
            if self.record:
                self._record_call_node(node, parsed, endpoint)
        return frozenset(Taint(endpoint) for endpoint in endpoints)

    def _is_local_client_wrapper(self, func: ast.Attribute) -> bool:
        """Do not treat ``self.helpers.get(...)`` as HTTP just because of the name.

        ``self.session.get(...)`` must still count as HTTP, so the check is on the
        receiver's name, not merely on the presence of ``self``.
        """
        value = func.value
        if isinstance(value, ast.Attribute):
            return not _client_like(value)
        if isinstance(value, ast.Name):
            return value.id == "self"
        return False

    def _record_unmapped(self, node: ast.Call, method: str, url_expr: ast.AST, path: str | None = None) -> None:
        if not self.record:
            return
        line, col = _pos(node)
        template = path if path is not None else self._url_raw(url_expr)
        key = (self.rel, line, col, method, template)
        if key in self.scanner._unmapped_seen:
            return
        self.scanner._unmapped_seen.add(key)
        self.scanner.unmapped.append(
            CallSite(
                file=self.rel,
                line=line,
                col=col,
                method=method.upper(),
                path_template=template or "<dynamic>",
                endpoint_key=None,
                expression=_truncate(ast.unparse(node)),
                function=self.label,
            )
        )

    def _record_call_node(self, node: ast.Call, parsed: UrlParse, endpoint: str) -> None:
        line, col = _pos(node)
        keywords = tuple(sorted(k.arg for k in node.keywords if k.arg))
        json_keys: tuple[str, ...] | None = None
        json_pos: tuple[int, int] | None = None  # (line, 0-based column) of the dict literal
        body = None
        for keyword in node.keywords:
            if keyword.arg == "json":
                body = keyword.value
            elif keyword.arg == "data" and body is None:
                body = keyword.value
        if body is None and len(node.args) >= 2 and parsed.method in {"post", "put", "patch"}:
            body = node.args[1]
        if isinstance(body, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in body.keys if k is not None) and body.keys and len(body.keys) == len(body.values):
            json_keys = tuple(str(k.value) for k in body.keys if isinstance(k, ast.Constant))
            json_pos = (body.lineno, body.col_offset)
        self.scanner.record_call(
            CallSite(
                file=self.rel,
                line=line,
                col=col,
                method=parsed.method,
                path_template=parsed.path,
                endpoint_key=endpoint,
                expression=_truncate(ast.unparse(node)),
                function=self.label,
                keywords=keywords,
                json_keys=json_keys,
                json_dict_pos=json_pos,
                url_literals=parsed.literals,
            )
        )

    def _url_raw(self, expr: ast.AST) -> str:
        parts = self._static_parts(expr)
        if parts is None:
            return "<dynamic>"
        return "".join(text if text is not None else "{*}" for text, _ in parts)

    def _parse_url(self, expr: ast.AST, method: str) -> UrlParse | None:
        parts = self._static_parts(expr)
        if parts is None:
            return None
        literals: list[LiteralRef] = []
        built = ""
        dynamic_index = 0
        for text, node in parts:
            if text is None:
                built += "{p%d}" % dynamic_index
                dynamic_index += 1
                continue
            built += text
            if node is not None and text:
                line, col = _pos(node)
                literals.append(LiteralRef(line=line, col=col, value=text))
        if not built:
            return None
        # Reduce an absolute URL to its path template.
        marker = "://"
        if marker in built:
            after = built.split(marker, 1)[1]
            slash = after.find("/")
            path = after[slash:] if slash >= 0 else "/"
        elif built.startswith("/"):
            path = built
        elif built.startswith("{") and "/" in built:
            # f"{base}/v1/customers": the leading dynamic segment is the origin.
            path = built[built.find("/"):]
        else:
            return None
        for sep in ("?", "#"):
            if sep in path:
                path = path.split(sep, 1)[0]
        if not path.startswith("/"):
            return None
        return UrlParse(path=path, literals=tuple(literals), method=method.upper())

    def _static_parts(self, expr: ast.AST, depth: int = 0) -> list[tuple[str | None, ast.AST | None]] | None:
        if depth > 12:
            return None
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str):
                return [(expr.value, expr)]
            return None
        if isinstance(expr, ast.JoinedStr):
            out: list[tuple[str | None, ast.AST | None]] = []
            for value in expr.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    out.append((value.value, value))
                elif isinstance(value, ast.FormattedValue):
                    if value.format_spec is not None:
                        out.append((None, None))
                        continue
                    inner = self._static_parts(value.value, depth + 1)
                    if inner is None:
                        out.append((None, None))
                    else:
                        out.extend((text, node) for text, node in inner if text is not None)
            return out
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left = self._static_parts(expr.left, depth + 1)
            right = self._static_parts(expr.right, depth + 1)
            if left is None or right is None:
                return None
            return left + right
        if isinstance(expr, ast.Name):
            if self.module and expr.id in self.module.consts:
                return self._static_parts(self.module.consts[expr.id], depth + 1)
            return None
        if isinstance(expr, ast.Attribute):
            # self.base_url / settings.base_url: origin unknown, treated as dynamic.
            return None
        if isinstance(expr, ast.Call):
            # e.g. urljoin(BASE, "/v1/x") is intentionally unsupported
            return None
        return None

    def _match_endpoints(self, method: str, path: str) -> list[str]:
        matches = []
        for key, op in self.scanner.model.operations.items():
            if op.method != method:
                continue
            if path_matches(path, op.path):
                matches.append(key)
        return sorted(matches)


def _truncate(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def scan_repository(root: Path, model: ApiModel, extra_roots: Iterable[Path] = ()) -> ScanResult:
    return RepoScanner(Path(root), model, extra_roots).scan()
