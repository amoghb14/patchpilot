from pathlib import Path

from patchpilot.engine.openapi import MigrationMetadata, diff_models, normalize, summarize
from patchpilot.engine.openapi.differ import BREAKING, RESPONSE_FIELD_RENAMED

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load_pair():
    old = normalize(FIXTURES / "acme_v1.yaml")
    new = normalize(FIXTURES / "acme_v2.yaml")
    metadata = MigrationMetadata.from_dict(
        __import__("json").loads((FIXTURES / "migration_metadata.json").read_text())
    )
    return old, new, metadata


def test_flattening_produces_dotted_paths():
    old, _, _ = load_pair()
    customer = old.operation("GET /v1/customers/{customer_id}")
    assert customer is not None
    assert customer.responses["200"]["full_name" if False else "name"].type == "string"
    listing = old.operation("GET /v1/customers")
    assert "data[].email" in listing.responses["200"]
    assert "data" in listing.responses["200"]


def test_explicit_renames_align_and_cascade():
    old, new, metadata = load_pair()
    changes = diff_models(old, new, metadata)
    renames = [c for c in changes if c.kind == RESPONSE_FIELD_RENAMED]
    paths = {(c.path, c.field_path) for c in renames}
    assert ("/v1/customers", "results") in paths
    assert ("/v1/customers", "results[].full_name") in paths
    assert ("/v1/customers/{customer_id}", "full_name") in paths
    for change in renames:
        assert change.confidence == "explicit"
        assert change.auto_fixable is True
        assert change.severity == BREAKING


def test_removed_field_and_required_body_field():
    old, new, metadata = load_pair()
    changes = diff_models(old, new, metadata)
    removed = [c for c in changes if c.kind == "response_field_removed"]
    assert any(c.field_path == "email" and c.auto_fixable for c in removed)

    required = [c for c in changes if c.kind == "request_field_required_added"]
    assert [c.field_path for c in required] == ["change_reason"]
    assert required[0].auto_fixable is True
    assert required[0].meta["value_expr"] == '"patchpilot migration"'


def test_endpoint_moves_and_removals():
    old, new, metadata = load_pair()
    changes = diff_models(old, new, metadata)
    moved = [c for c in changes if c.kind == "endpoint_path_changed"]
    assert [c.meta["new_endpoint"] for c in moved] == ["GET /v2/invoices/{invoice_id}"]
    removed = [c for c in changes if c.kind == "endpoint_removed"]
    assert [c.endpoint_key for c in removed] == ["DELETE /v1/customers/{customer_id}"]


def test_type_and_enum_changes_are_flagged_for_review():
    old, new, metadata = load_pair()
    changes = diff_models(old, new, metadata)
    type_changes = [c for c in changes if c.kind == "response_field_type_changed"]
    assert any(c.field_path == "created_at" and c.old == "string" and c.new == "integer" for c in type_changes)
    enum_removed = [c for c in changes if c.kind == "response_enum_value_removed"]
    status = [c for c in enum_removed if c.field_path == "status"]
    assert status and status[0].meta["removed_values"] == ["draft"]
    assert all(c.requires_review for c in type_changes + enum_removed)


def test_unauthorised_rename_is_inferred_but_not_auto_fixable():
    old = normalize(
        {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/v1/things": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "untouched": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )
    new = normalize(
        {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "2"},
            "paths": {
                "/v1/things": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "full_name": {"type": "string"},
                                                "untouched": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )
    changes = diff_models(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert change.kind == RESPONSE_FIELD_RENAMED
    assert change.confidence == "inferred"
    assert change.auto_fixable is False
    assert change.requires_review is True


def test_no_false_rename_for_unrelated_field():
    old = normalize(
        {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/v1/things": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"alpha": {"type": "string"}, "beta": {"type": "integer"}},
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )
    new = normalize(
        {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "2"},
            "paths": {
                "/v1/things": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"zeta": {"type": "string"}, "beta": {"type": "integer"}},
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )
    changes = diff_models(old, new)
    kinds = sorted(c.kind for c in changes)
    assert kinds == ["response_field_added", "response_field_removed"]


def test_summary_counts():
    old, new, metadata = load_pair()
    stats = summarize(diff_models(old, new, metadata))
    assert stats["by_severity"]["breaking"] > 0
    assert stats["requires_review"] >= 1
    assert stats["auto_fixable"] >= 3
