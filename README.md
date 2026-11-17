# PatchPilot

**Detect breaking API changes, find the Python code that depends on them, and generate a migration your tests prove.**

PatchPilot takes two OpenAPI contracts (an API's old and new versions), computes a semantic
diff, traces every breaking change into a real repository with an AST-level taint analysis,
and writes a source-preserving patch with [LibCST](https://libcst.readthedocs.io/). Then it
proves the patch: the repository's test suite is run twice in a disposable sandbox — once
unpatched against the new contract (it must fail) and once patched (it must pass).

The interesting part is not "an LLM edits code". It is the evidence chain: a change is only
auto-applied when *metadata authorises it* **and** *the analyser can prove a given field read
comes from that specific endpoint*.

---

## What it does

```
old spec ─┐
          ├─▶ semantic diff ──▶ breaking changes ─┐
new spec ─┘   (rename-aware)                     │
                                                 ├─▶ affected code ──▶ patch ──▶ verify
repository ─────▶ AST taint analysis ────────────┘   (planner)      (LibCST)   (sandboxed tests)
```

1. **Diff** the contracts: removed/renamed fields, type and enum changes, newly required request
   fields, moved or deleted endpoints. Renames are aligned *cascadingly* — if `data` becomes
   `results`, then `data[].name → results[].full_name` is discovered relative to the renamed parent.
2. **Locate** the damage: recognise outbound HTTP calls, parse their URL into a path template that
   is matched to an OpenAPI operation, then follow `.json()`, subscripts, loops and comprehensions
   to every field read. Values that did not come from an API response are never touched.
3. **Plan** a minimal edit set. Missing metadata means "report, don't rewrite".
4. **Transform** with LibCST, addressed by exact line/column, so comments, ordering, quotes and
   unrelated code survive byte-for-byte.
5. **Verify** by running the suite before and after in a fresh temp copy of the repository.

### The demo, end to end

`examples/acme_client` is a small customer/invoice app written against Acme API **v1.4.0**.
`fixtures/acme_v2.yaml` is the breaking **2.0.0** release; `examples/acme_client/mock_api.py`
serves either contract so the breakage is real, not simulated with mocks.

| Step | Result |
| --- | --- |
| Contract diff | 30 changes, 21 breaking |
| Code analysis | 13 Python files, 5 call sites, 17 affected locations |
| Affected files | `app/reports.py`, `app/client.py` — and **not** `app/legacy.py`, whose local `customer` dicts are the false-positive trap |
| Generated patch | 7 edits in 2 files |
| Verification | baseline: **5 failed** → patched: **9 passed** (`verified`) |

The patch itself is intentionally small:

```diff
-    email = customer["email"]
+    email = customer.get("email", None)
-        "display_name": customer["name"].strip(),
+        "display_name": customer["full_name"].strip(),
-    for customer in payload["data"]:
+    for customer in payload["results"]:
-            json={"plan": plan},
+            json={"plan": plan, "change_reason": "patchpilot migration"},
-        response = self.session.get(f"{self.base_url}/v1/invoices/{invoice_id}")
+        response = self.session.get(f"{self.base_url}/v2/invoices/{invoice_id}")
```

What PatchPilot deliberately does *not* do here: `DELETE /v1/customers/{customer_id}` was removed,
`Customer.created_at` changed from ISO string to epoch seconds, and the `plan`/`status` enums lost
values. All three are reported as breaking and sent to review with a reason — there is no safe
mechanical rewrite, and guessing one is how migration bots break production.

---

## Quickstart

Requires Python 3.11+ and Node 20+.

```bash
cd ~/Downloads/patchpilot

# 1. backend
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 2. seed the demo (repository + both specs + scan + migration + verification)
.venv/bin/python scripts/seed_demo.py

# 3. API on :8000
cd backend && ../.venv/bin/uvicorn patchpilot.main:app --reload --port 8000

# 4. dashboard on :3000 (separate shell)
cd frontend && npm install && npm run dev
```

Open <http://localhost:3000>, or the API docs at <http://127.0.0.1:8000/docs>.

The seed prints a scan id; the dashboard also links it.

### Run the tests

```bash
cd backend && ../.venv/bin/python -m pytest        # 32 tests
cd frontend && npm run typecheck && npm run build
```

### Reproduce the benchmark

```bash
.venv/bin/python scripts/benchmark.py             # writes benchmarks/latest.json
```

The dashboard reads that artifact through `GET /api/benchmarks/latest`; nothing is hard-coded.

---

## Measured results

From `benchmarks/latest.json` (Python 3.11, macOS arm64), generated corpus with ground truth:

| Metric | Value |
| --- | --- |
| Labelled field locations | 400 |
| True positives / false positives / false negatives | 400 / **0** / **0** |
| Precision / recall / F1 | **1.000** / **1.000** / **1.000** |
| Throughput | ~1,060 files/s (0.94 ms per file) |
| Changes detected | 6 breaking |
| Migration verification | `verified` — 5 failing before, 9 passing after |

The corpus is generated, so this measures the analyser against *known* labels including decoys
(local dictionaries with identical field names) — it is not a claim about arbitrary real-world
repositories. Scaling runs (25 / 100 / 250 modules) are in the artifact as well.

---

## How the analysis works

### 1. Rename-aware contract alignment (`engine/openapi/differ.py`)

Schemas are flattened to dotted paths (`data[].full_name`). Before comparing, fields are aligned:

* if the new path exists in the old schema, it is the same field;
* otherwise, if a metadata rule says `name → full_name`, the candidate old path
  `parent + "name"` is checked — and the *new* path is recorded as a rename, so children are
  compared relative to the renamed parent;
* otherwise a similarity heuristic (`name` vs `full_name`: token containment beats edit distance)
  may propose a rename, but it is emitted with `confidence="inferred"`, `auto_fixable=false`,
  `requires_review=true`.

Only `confidence="explicit"` renames ever produce an edit.

### 2. Taint analysis (`engine/analysis/scanner.py`)

Bounded, intra-procedural, with two inter-procedural extensions:

* **HTTP detection** is structural (`requests.get`, `session.post`, `httpx.Client.delete`, …) and the
  URL is statically folded (literals, f-strings, `+`, module constants) into a path template that is
  matched segment-by-segment against the spec, with `{}` matching any segment.
* **Taint propagation** follows assignments, `.json()`, `.get(...)`, subscripts, loops,
  comprehensions, `with` bindings and attribute chains. `payload["data"][0]["name"]` becomes
  `data[].name`, exactly the vocabulary the differ uses.
* **Return summaries**: a wrapper returning `response.json()` is itself treated as tainted at call
  sites, iterated to a fixpoint (max 4 rounds).
* **Parameter injection**: passing tainted data into a local function re-analyses that function with
  the parameter tainted, so `summarise(client.get_customer(id))` is covered.

Field reads on values that are not tainted (`legacy.py`'s CSV dicts, cache lookups) produce nothing.
Calls whose URL cannot be statically resolved are listed as `unmapped_calls` instead of being guessed.

### 3. Planning and transformation (`engine/migration/`)

`planner.py` turns findings into `CodeEdit`s only when the change is auto-fixable **and** the access
path matches exactly (an ancestor rename is migrated at the ancestor's own literal, never at a
descendant's key). Edits:

| Change | Edit |
| --- | --- |
| Explicit rename | replace the key/attribute literal |
| Removed field + `optional_access` rule | `x["field"]` → `x.get("field", None)` |
| Newly required request field | insert the key into the inline body dict |
| Moved endpoint | rewrite only the differing static URL segment (`/v1/` → `/v2/`) |

`transformer.py` applies them via LibCST with `PositionProvider`, so edits are surgical. Every
result is re-parsed and `compile()`d; an edit whose node cannot be found is reported as `not_found`
rather than silently skipped, and a file that fails to parse is never written.

### 4. Verification (`engine/verification/`)

Two runs in separate temp copies: baseline (unpatched, new-contract environment) must fail, patched
must pass. Verdicts are explicit: `verified`, `baseline_passed` (the suite cannot prove anything),
`patch_failed`, `timeout`. A PR can only be opened for a `verified` migration.

---

## Architecture

```
backend/
  patchpilot/
    engine/
      openapi/       loader (YAML/JSON, $ref, allOf, flattening), differ, migration metadata
      analysis/      HTTP call detection, taint scanner, finding matcher
      migration/     planner, LibCST transformer
      verification/  sandbox, two-run verifier
    services/        scan → migration → verification orchestration
    integrations/    GitHub import (tarball) and pull requests (REST)
    models.py        SQLAlchemy 2.0 schema (repositories, specs, scans, findings,
                     locations, migrations, verifications, pull requests)
    api/router.py    FastAPI routes + serializers
    main.py          app factory, CORS, lifespan
  tests/             32 tests: diff semantics, taint analysis, patch safety, API, end-to-end verify
frontend/            Next.js 14 + TypeScript + Tailwind + Monaco diff viewer
examples/acme_client demo target repo (client, reports, invoices, false-positive traps, tests)
fixtures/            Acme v1/v2 specs and explicit migration metadata
scripts/             seed_demo.py, benchmark.py
benchmarks/latest.json  generated metrics artifact (committed)
```

### API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | status + engine capabilities |
| POST | `/api/repositories` | import a local path (allow-listed roots) or `owner/repo` |
| GET | `/api/repositories` | list |
| POST | `/api/specs` | store an OpenAPI document (deduped by hash) |
| GET | `/api/specs` | list |
| POST | `/api/diffs` | stateless semantic diff |
| POST | `/api/scans` | diff + code analysis for a repository/spec pair |
| GET | `/api/scans`, `/api/scans/{id}` | list / full report with locations |
| POST | `/api/scans/{id}/migration` | plan + apply edits (no writes to your repo) |
| GET | `/api/migrations/{id}` | patch, diff, skipped edits, last verification |
| POST | `/api/migrations/{id}/verify` | run the suite before/after |
| POST | `/api/migrations/{id}/pull-request` | open a PR (verified migrations only) |
| GET | `/api/benchmarks/latest` | metrics artifact |

---

## Safety model

* **Repository roots are allow-listed** (`PATCHPILOT_ALLOWED_REPO_ROOTS`, default: home, `/tmp`,
  project root). Absolute paths only; `/etc` is rejected.
* **No shell**: test commands are parsed with `shlex`, shell metacharacters are rejected, and the
  executable is checked against an allow-list. No `shell=True` anywhere.
* **Patch containment**: patched files are written into a temp copy whose paths are re-validated;
  archives are extracted with traversal checks.
* **Secrets stay server-side**: GitHub tokens come from `PATCHPILOT_GITHUB_TOKEN` and are never
  returned by an API, logged, or written into a workspace.
* **Honest limits**: process isolation is a temp directory + timeout + process group kill. It is not
  a security boundary for untrusted code — run the runner inside a network-less container for that.
  This is documented in `engine/verification/sandbox.py`.

## Limitations

* Python repositories only (the analyser is `ast`-based); JavaScript/TypeScript is the obvious next
  step and would be a separate analyser behind the same `ScanResult` contract.
* One-hop method resolution and 4 summary/injection rounds bound the interprocedural analysis;
  dynamic dispatch, `**kwargs` forwarding and `urljoin`-built URLs are reported as unmapped.
* Migrations cover renames, removals (with an authorised optional-access strategy), newly required
  request fields, and endpoint moves. Type and enum changes are detected and routed to review.
* SQLite by default; set `PATCHPILOT_DATABASE_URL=postgresql+psycopg://…` for PostgreSQL. No Alembic
  migrations yet — the schema is created on startup.

## Roadmap

1. JavaScript/TypeScript analyser sharing the same change vocabulary.
2. Docker executor for verification (swap `run_command`), with per-language images.
3. Durable scan queue + webhook triggers on API changelog feeds.
4. Multi-repo impact graph: one contract change, many consumers.
5. Suggested migration code for type/enum changes with human-approved templates.
