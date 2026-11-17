import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from patchpilot.main import app

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "acme_client"
FIXTURES = ROOT / "fixtures"

client = TestClient(app)


@pytest.fixture(scope="module")
def seeded():
    repo = client.post(
        "/api/repositories", json={"name": "acme-client", "source_type": "local", "source": str(EXAMPLES)}
    )
    assert repo.status_code == 201, repo.text
    old = client.post(
        "/api/specs",
        json={"name": "acme", "version_label": "1.4.0", "content": (FIXTURES / "acme_v1.yaml").read_text()},
    )
    new = client.post(
        "/api/specs",
        json={"name": "acme", "version_label": "2.0.0", "content": (FIXTURES / "acme_v2.yaml").read_text()},
    )
    assert old.status_code == 201 and new.status_code == 201, (old.text, new.text)
    scan = client.post(
        "/api/scans",
        json={
            "repository_id": repo.json()["id"],
            "old_spec_id": old.json()["id"],
            "new_spec_id": new.json()["id"],
            "metadata": json.loads((FIXTURES / "migration_metadata.json").read_text()),
        },
    )
    assert scan.status_code == 201, scan.text
    return {"repo": repo.json(), "old": old.json(), "new": new.json(), "scan": scan.json()}


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_scan_reports_breaking_changes_and_affected_code(seeded):
    scan = seeded["scan"]
    stats = scan["stats"]
    assert stats["diff"]["by_severity"]["breaking"] >= 5
    assert stats["code"]["call_sites"] >= 5
    assert stats["matched"]["affected_locations"] >= 5

    renames = [f for f in scan["findings"] if f["kind"] == "response_field_renamed"]
    assert renames and all(f["confidence"] == "explicit" for f in renames)
    files = {loc["file"] for f in scan["findings"] for loc in f["locations"]}
    assert "app/legacy.py" not in files
    assert {"app/reports.py", "app/client.py"} <= files


def test_migration_generation_and_verification(seeded):
    scan_id = seeded["scan"]["id"]
    migration = client.post(f"/api/scans/{scan_id}/migration")
    assert migration.status_code == 201, migration.text
    payload = migration.json()
    assert payload["stats"]["files_changed"] == 2
    assert payload["stats"]["failed"] == []
    assert "change_reason" in payload["diff"]
    assert "/v2/invoices/" in payload["diff"]

    verification = client.post(
        f"/api/migrations/{payload['id']}/verify",
        json={"test_command": "python -m pytest -q", "env": {"ACME_API_MODE": "v2"}, "timeout_seconds": 120},
    )
    assert verification.status_code == 201, verification.text
    result = verification.json()
    assert result["verdict"] == "verified", result
    assert result["baseline"]["status"] == "failed"
    assert result["patched"]["status"] == "passed"


def test_pull_request_requires_verified_migration(seeded):
    scan_id = seeded["scan"]["id"]
    migration = client.post(f"/api/scans/{scan_id}/migration").json()
    blocked = client.post(
        f"/api/migrations/{migration['id']}/pull-request",
        json={"repo_full_name": "acme/client"},
    )
    assert blocked.status_code == 409


def test_diff_playground():
    response = client.post(
        "/api/diffs",
        json={
            "old_spec": (FIXTURES / "acme_v1.yaml").read_text(),
            "new_spec": (FIXTURES / "acme_v2.yaml").read_text(),
            "metadata": json.loads((FIXTURES / "migration_metadata.json").read_text()),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["specs"]["old"]["operations"] == 6
    assert any(change["kind"] == "endpoint_path_changed" for change in body["changes"])


def test_invalid_spec_is_rejected():
    response = client.post("/api/specs", json={"name": "nope", "content": "paths: {}"})
    assert response.status_code == 422


def test_repository_outside_allowed_roots_is_rejected():
    response = client.post("/api/repositories", json={"source_type": "local", "source": "/etc"})
    assert response.status_code in (403, 404)
    assert "allowed" in response.text or "does not exist" in response.text


def test_relative_repository_path_is_rejected():
    response = client.post("/api/repositories", json={"source_type": "local", "source": "examples/acme_client"})
    assert response.status_code == 400


def test_bad_github_name_is_rejected():
    response = client.post("/api/repositories", json={"source_type": "github", "source": "../../etc/passwd"})
    assert response.status_code == 400


def test_verification_command_rejects_shell_metacharacters(seeded):
    scan_id = seeded["scan"]["id"]
    migration = client.post(f"/api/scans/{scan_id}/migration").json()
    response = client.post(
        f"/api/migrations/{migration['id']}/verify",
        json={"test_command": "python -m pytest; rm -rf /", "env": {}},
    )
    assert response.status_code == 400
    assert "metacharacters" in response.text


def test_disallowed_executable_is_rejected(seeded):
    scan_id = seeded["scan"]["id"]
    migration = client.post(f"/api/scans/{scan_id}/migration").json()
    response = client.post(
        f"/api/migrations/{migration['id']}/verify",
        json={"test_command": "curl http://example.com", "env": {}},
    )
    assert response.status_code == 400
