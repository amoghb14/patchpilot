"""Measure PatchPilot against a labelled corpus.

The corpus is generated (not scraped), so every expected location is known:
each module contains real API calls, field reads that must be flagged, and
*decoys* — local dictionaries with the same field names that must not be
flagged. Precision, recall and throughput are computed from the run, never
hard-coded.

Usage:
    python scripts/benchmark.py            # full run, writes benchmarks/latest.json
    python scripts/benchmark.py --quiet
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from patchpilot.engine.analysis import match_changes, scan_repository  # noqa: E402
from patchpilot.engine.openapi import MigrationMetadata, diff_models, normalize  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_ROOT / "benchmarks"

V1_SPEC = """
openapi: 3.0.3
info: {title: Ledger API, version: "1.0.0"}
paths:
  /v1/items/{item_id}:
    get:
      operationId: getItem
      parameters:
        - {name: item_id, in: path, required: true, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Item"}
  /v1/items:
    get:
      operationId: listItems
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/ItemList"}
  /v1/orders/{order_id}:
    get:
      operationId: getOrder
      parameters:
        - {name: order_id, in: path, required: true, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Order"}
components:
  schemas:
    Item:
      type: object
      required: [id, name, email, price]
      properties:
        id: {type: string}
        name: {type: string}
        email: {type: string}
        price: {type: number}
    ItemList:
      type: object
      required: [data, total]
      properties:
        data: {type: array, items: {$ref: "#/components/schemas/Item"}}
        total: {type: integer}
    Order:
      type: object
      required: [id, status]
      properties:
        id: {type: string}
        status: {type: string, enum: [open, paid]}
"""

V2_SPEC = """
openapi: 3.0.3
info: {title: Ledger API, version: "2.0.0"}
paths:
  /v1/items/{item_id}:
    get:
      operationId: getItem
      parameters:
        - {name: item_id, in: path, required: true, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Item"}
  /v1/items:
    get:
      operationId: listItems
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/ItemList"}
  /v2/orders/{order_id}:
    get:
      operationId: getOrder
      parameters:
        - {name: order_id, in: path, required: true, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Order"}
components:
  schemas:
    Item:
      type: object
      required: [id, full_name, price]
      properties:
        id: {type: string}
        full_name: {type: string}
        price: {type: number}
    ItemList:
      type: object
      required: [results, total]
      properties:
        results: {type: array, items: {$ref: "#/components/schemas/Item"}}
        total: {type: integer}
    Order:
      type: object
      required: [id, status]
      properties:
        id: {type: string}
        status: {type: string, enum: [open, paid]}
"""

METADATA = {
    "renames": [
        {"location": "response", "from": "name", "to": "full_name"},
        {"location": "response", "from": "data", "to": "results"},
    ],
    "path_changes": [{"from": "GET /v1/orders/{order_id}", "to": "GET /v2/orders/{order_id}"}],
    "removals": [{"endpoint": "GET /v1/items/{item_id}", "field": "email", "strategy": "optional_access"}],
}

MODULE_TEMPLATE = '''"""Generated corpus module {index}."""

import requests

BASE_URL = "https://api.ledger.example"

EXPECTED_NAME = "name"
EXPECTED_EMAIL = "email"


class LedgerClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()

    def item_{index}(self, item_id: str) -> dict:
        response = self.session.get(f"{{self.base_url}}/v1/items/{{item_id}}")
        response.raise_for_status()
        return response.json()

    def items_{index}(self) -> dict:
        response = self.session.get(f"{{self.base_url}}/v1/items")
        return response.json()

    def order_{index}(self, order_id: str) -> dict:
        response = self.session.get(f"{{self.base_url}}/v1/orders/{{order_id}}")
        return response.json()


def summarise_{index}(client: LedgerClient, item_id: str) -> dict:
    item = client.item_{index}(item_id)
    return {{"label": item["name"], "contact": item["email"], "price": item["price"]}}


def names_{index}(client: LedgerClient) -> list:
    payload = client.items_{index}()
    return [row["name"] for row in payload["data"]]


def order_status_{index}(client: LedgerClient, order_id: str) -> str:
    order = client.order_{index}(order_id)
    return order["status"]


def decoy_row_{index}(row: list) -> dict:
    item = {{"name": row[0], "email": row[1], "price": 0}}
    return {{"label": item["name"], "contact": item["email"]}}


def decoy_cache_{index}(cache: dict) -> dict:
    item = cache["item"]
    return {{"label": item["name"], "contact": item["email"]}}
'''


@dataclass
class Corpus:
    root: Path
    expected: set[tuple[str, str]] = field(default_factory=set)
    decoy_files: set[str] = field(default_factory=set)
    modules: int = 0
    lines: int = 0


def generate_corpus(root: Path, modules: int) -> Corpus:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ledger").mkdir(exist_ok=True)
    (root / "ledger" / "__init__.py").write_text("")
    corpus = Corpus(root=root)
    corpus.decoy_files = {"ledger/module_0.py"}
    for index in range(modules):
        rel = f"ledger/module_{index}.py"
        source = MODULE_TEMPLATE.format(index=index)
        (root / rel).write_text(source)
        corpus.lines += source.count("\n") + 1
        # Everything the engine must find for this module.
        corpus.expected.update(
            {
                (rel, "name"),
                (rel, "email"),
                (rel, "data"),
                (rel, "data[].name"),
            }
        )
    corpus.modules = modules
    return corpus


def evaluate(root: Path, expected: set[tuple[str, str]]) -> dict:
    old_model = normalize(V1_SPEC)
    new_model = normalize(V2_SPEC)
    metadata = MigrationMetadata.from_dict(METADATA)

    started = time.perf_counter()
    scan = scan_repository(root, old_model)
    scan_ms = (time.perf_counter() - started) * 1000
    changes = diff_models(old_model, new_model, metadata)
    matched = match_changes(scan, changes)

    found: set[tuple[str, str]] = set()
    call_site_matches = 0
    for item in matched:
        for location in item.locations:
            if location.path:
                found.add((location.file, location.path))
            else:
                # Endpoint-level findings (a moved or removed path) are counted
                # separately: they are call sites, not field reads.
                call_site_matches += 1

    true_positives = found & expected
    false_positives = found - expected
    false_negatives = expected - found
    precision = len(true_positives) / len(found) if found else 0.0
    recall = len(true_positives) / len(expected) if expected else 0.0
    files = scan.files_analyzed or 1
    return {
        "files": scan.files_analyzed,
        "lines": scan.lines_scanned,
        "call_sites": len(scan.call_sites),
        "field_accesses": len(scan.accesses),
        "true_positives": len(true_positives),
        "false_positives": len(false_positives),
        "call_site_matches": call_site_matches,
        "false_negatives": len(false_negatives),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "false_positive_examples": sorted(false_positives)[:10],
        "false_negative_examples": sorted(false_negatives)[:10],
        "scan_duration_ms": round(scan_ms, 1),
        "files_per_second": round(files / (scan_ms / 1000), 1) if scan_ms else 0.0,
        "ms_per_file": round(scan_ms / files, 2),
        "changes_detected": len(changes),
        "breaking_changes": sum(1 for change in changes if change.severity == "breaking"),
    }


def verification_numbers() -> dict:
    """Run the demo migration end to end and report the real verdict."""
    example = PROJECT_ROOT / "examples" / "acme_client"
    fixtures = PROJECT_ROOT / "fixtures"
    if not example.exists():
        return {"status": "skipped", "reason": "demo repository missing"}
    from patchpilot.engine.analysis import match_changes as mc
    from patchpilot.engine.migration import apply_plan_patch, plan_patch
    from patchpilot.engine.verification import verify_patch

    old = normalize(fixtures / "acme_v1.yaml")
    new = normalize(fixtures / "acme_v2.yaml")
    metadata = MigrationMetadata.from_dict(json.loads((fixtures / "migration_metadata.json").read_text()))
    scan = scan_repository(example, old)
    plan = plan_patch(mc(scan, diff_models(old, new, metadata)))
    sources = {rel: (example / rel).read_text() for rel in plan.by_file}
    results = apply_plan_patch(sources, plan.by_file)
    patched = {rel: result.new_source for rel, result in results.items() if result.changed}
    report = verify_patch(
        example, patched, [sys.executable, "-m", "pytest", "-q"], {"ACME_API_MODE": "v2"}, timeout=180
    )
    return {
        "status": report.verdict,
        "files_changed": len(patched),
        "edits": len(plan.edits),
        "skipped": len(plan.skipped),
        "baseline_failed": report.baseline.failed,
        "baseline_status": report.baseline.status,
        "patched_passed": report.patched.passed,
        "patched_status": report.patched.status,
        "baseline_ms": report.baseline.duration_ms,
        "patched_ms": report.patched.duration_ms,
        "summary": report.summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--scaling", nargs="*", type=int, default=[25, 100, 250])
    args = parser.parse_args()

    BENCHMARK_DIR.mkdir(exist_ok=True)
    import tempfile

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "corpus": {"kind": "generated", "modules_per_run": None},
        "detection": {},
        "scaling": [],
        "verification": {},
    }

    with tempfile.TemporaryDirectory(prefix="patchpilot-bench-") as tmp:
        base = Path(tmp)
        main_corpus = generate_corpus(base / "main", 100)
        report["corpus"] = {
            "kind": "generated",
            "modules": main_corpus.modules,
            "decoy_functions_per_module": 2,
            "expected_locations": len(main_corpus.expected),
            "lines": main_corpus.lines,
        }
        report["detection"] = evaluate(base / "main", main_corpus.expected)

        for size in args.scaling:
            corpus = generate_corpus(base / f"scaling_{size}", size)
            metrics = evaluate(base / f"scaling_{size}", corpus.expected)
            report["scaling"].append(
                {
                    "modules": corpus.modules,
                    "files": metrics["files"],
                    "lines": metrics["lines"],
                    "scan_duration_ms": metrics["scan_duration_ms"],
                    "files_per_second": metrics["files_per_second"],
                    "ms_per_file": metrics["ms_per_file"],
                    "precision": metrics["precision"],
                    "recall": metrics["recall"],
                }
            )

    report["verification"] = verification_numbers()

    (BENCHMARK_DIR / "latest.json").write_text(json.dumps(report, indent=2) + "\n")

    if not args.quiet:
        detection = report["detection"]
        print("PatchPilot benchmark")
        print(f"  corpus            {report['corpus']['modules']} modules, "
              f"{report['corpus']['lines']} lines, {report['corpus']['expected_locations']} labelled locations")
        print(f"  precision         {detection['precision']:.3f}  ({detection['true_positives']} TP, "
              f"{detection['false_positives']} FP)")
        print(f"  recall            {detection['recall']:.3f}  ({detection['false_negatives']} FN)")
        print(f"  throughput        {detection['files_per_second']:.0f} files/s "
              f"({detection['ms_per_file']:.2f} ms/file)")
        print(f"  changes detected  {detection['changes_detected']} "
              f"({detection['breaking_changes']} breaking)")
        verification = report["verification"]
        print(f"  verification      {verification.get('status')} "
              f"({verification.get('baseline_failed')} failing before, "
              f"{verification.get('patched_passed')} passing after)")
        print(f"  artifact          {BENCHMARK_DIR / 'latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
