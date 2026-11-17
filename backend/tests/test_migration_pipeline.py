import json
import sys
from pathlib import Path

import pytest

from patchpilot.engine.analysis import match_changes, scan_repository
from patchpilot.engine.migration import apply_plan_patch, plan_patch, rewrite_url_literal
from patchpilot.engine.migration.planner import CodeEdit, REPLACE_STRING
from patchpilot.engine.openapi import MigrationMetadata, diff_models, normalize
from patchpilot.engine.verification import verify_patch

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "acme_client"
FIXTURES = ROOT / "fixtures"


def build_plan():
    old = normalize(FIXTURES / "acme_v1.yaml")
    new = normalize(FIXTURES / "acme_v2.yaml")
    metadata = MigrationMetadata.from_dict(json.loads((FIXTURES / "migration_metadata.json").read_text()))
    scan = scan_repository(EXAMPLES, old)
    matched = match_changes(scan, diff_models(old, new, metadata))
    return scan, matched, plan_patch(matched)


def patched_sources():
    plan = build_plan()[2]
    sources = {rel: (EXAMPLES / rel).read_text() for rel in plan.by_file}
    results = apply_plan_patch(sources, plan.by_file)
    return plan, results, {rel: result.new_source for rel, result in results.items() if result.changed}


def test_findings_map_contract_changes_to_the_right_files():
    _, matched, _ = build_plan()
    by_kind: dict[str, set[str]] = {}
    for item in matched:
        by_kind.setdefault(item.change.kind, set()).update(loc.file for loc in item.locations)
    assert by_kind["response_field_renamed"] <= {"app/reports.py"}
    assert by_kind["response_field_removed"] <= {"app/reports.py"}
    assert by_kind["endpoint_path_changed"] == {"app/client.py"}
    assert "app/legacy.py" not in set().union(*by_kind.values())


def test_plan_contains_only_authorised_edits():
    plan = build_plan()[2]
    files = {edit.file for edit in plan.edits}
    assert files == {"app/client.py", "app/reports.py"}
    assert any(edit.kind == "insert_dict_key" and edit.new_value == "change_reason" for edit in plan.edits)
    assert any(edit.kind == "optional_subscript" for edit in plan.edits)
    assert any(edit.kind == "replace_string" and edit.new_value == "results" for edit in plan.edits)
    reasons = " ".join(skip.reason for skip in plan.skipped)
    assert "review" in reasons or "removed" in reasons or "no mechanical" in reasons
    # Additive changes cannot break a caller, so they must not be queued as work.
    assert all(
        skip.change_kind not in {"response_field_added", "response_enum_value_added", "endpoint_added"}
        for skip in plan.skipped
    )


def test_transformation_preserves_unrelated_code():
    plan, results, patched = patched_sources()
    for result in results.values():
        assert result.failures == [], result.failures
    reports = patched["app/reports.py"]
    assert 'customer["full_name"].strip()' in reports
    assert 'customer.get("email", None)' in reports
    assert 'customer["results"]' not in reports
    assert 'payload["results"]' in reports
    client = patched["app/client.py"]
    assert "change_reason" in client
    assert "/v2/invoices/" in client
    # unrelated modules are not touched at all
    assert "app/legacy.py" not in patched
    assert (EXAMPLES / "app/legacy.py").read_text() == (
        ROOT / "examples" / "acme_client" / "app" / "legacy.py"
    ).read_text()


def test_url_rewrite_only_swaps_the_version_segment():
    from patchpilot.engine.analysis.model import LiteralRef

    literal = LiteralRef(line=1, col=1, value="/v1/invoices/")
    rewritten = rewrite_url_literal((literal,), "/v1/invoices/{invoice_id}", "/v2/invoices/{invoice_id}")
    assert rewritten is not None
    assert rewritten[1] == "/v2/invoices/"
    assert rewrite_url_literal((literal,), "/v1/customers/{id}", "/v1/customers") is None


def test_verification_reproduces_breakage_and_confirms_the_patch():
    plan, _, patched = patched_sources()
    assert patched
    report = verify_patch(
        EXAMPLES,
        patched,
        [sys.executable, "-m", "pytest", "-q"],
        {"ACME_API_MODE": "v2"},
        timeout=120,
    )
    assert report.baseline.status == "failed", report.baseline.output
    assert report.patched.status == "passed", report.patched.output
    assert report.verdict == "verified"


def test_baseline_passing_is_reported_honestly():
    _, _, patched = patched_sources()
    report = verify_patch(
        EXAMPLES,
        patched,
        [sys.executable, "-m", "pytest", "-q"],
        {"ACME_API_MODE": "v1"},
        timeout=120,
    )
    assert report.baseline.status == "passed"
    assert report.verdict == "baseline_passed"


def test_edit_targeting_a_missing_node_is_reported_not_applied():
    from patchpilot.engine.migration import apply_edits

    source = "customer['name']\n"
    bogus = CodeEdit(
        id="x1",
        kind=REPLACE_STRING,
        file="app/x.py",
        line=9,
        col=4,
        change_id="c1",
        change_kind="response_field_renamed",
        old_value="nickname",
        new_value="full_name",
    )
    result = apply_edits(source, [bogus], "app/x.py")
    assert result.changed is False
    assert result.new_source == source
    assert result.failures and result.failures[0].status == "not_found"
