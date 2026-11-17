import json
from pathlib import Path

from patchpilot.engine.analysis import scan_repository
from patchpilot.engine.openapi import normalize

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "acme_client"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def scan():
    model = normalize(FIXTURES / "acme_v1.yaml")
    return scan_repository(EXAMPLES, model), model


def test_http_calls_are_mapped_to_operations():
    result, _ = scan()
    endpoints = {site.endpoint_key for site in result.call_sites}
    assert "GET /v1/customers/{customer_id}" in endpoints
    assert "GET /v1/customers" in endpoints
    assert "PATCH /v1/customers/{customer_id}" in endpoints
    assert "GET /v1/invoices/{invoice_id}" in endpoints
    assert "DELETE /v1/customers/{customer_id}" in endpoints
    assert result.files_analyzed >= 6
    assert result.syntax_errors == []


def test_response_fields_are_attributed_through_wrappers():
    result, _ = scan()
    names = {(a.file, a.field, a.path) for a in result.accesses}
    assert ("app/reports.py", "name", "name") in names
    assert ("app/reports.py", "email", "email") in names
    assert ("app/reports.py", "plan", "plan") in names
    assert ("app/reports.py", "data", "data") in names
    assert ("app/reports.py", "plan", "data[].plan") in names
    assert ("app/reports.py", "name", "data[].name") in names


def test_unrelated_local_dicts_are_not_tainted():
    result, _ = scan()
    legacy = [a for a in result.accesses if a.file == "app/legacy.py"]
    assert legacy == [], [a.path for a in legacy]


def test_body_and_keyword_metadata_is_captured():
    result, _ = scan()
    patch_site = next(s for s in result.call_sites if s.endpoint_key == "PATCH /v1/customers/{customer_id}")
    assert patch_site.json_keys == ("plan",)
    assert patch_site.json_dict_pos is not None
    assert "json" in patch_site.keywords


def test_url_literals_are_available_for_rewrites():
    result, _ = scan()
    invoice = next(s for s in result.call_sites if s.endpoint_key == "GET /v1/invoices/{invoice_id}")
    assert invoice.path_template == "/v1/invoices/{p1}"
    assert any("/v1/invoices/" in literal.value for literal in invoice.url_literals)


def test_access_expression_is_reported_for_humans():
    result, _ = scan()
    summary = [a for a in result.accesses if a.file == "app/reports.py" and a.field == "name"]
    assert summary
    # ast.unparse normalises quote style; what matters is the receiver and field.
    assert "customer" in summary[0].expression and "name" in summary[0].expression
    assert summary[0].access_kind == "subscript"
