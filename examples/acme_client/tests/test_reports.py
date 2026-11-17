from app.reports import (
    customer_summary,
    display_names,
    plan_breakdown,
    render_customer_page,
)


def test_summary_of_fetched_customer(client):
    summary = render_customer_page(client, "cus_1")
    assert summary["display_name"] == "Ada Lovelace"
    assert summary["plan"] == "pro"
    assert isinstance(summary["contact"], str)


def test_plan_breakdown_counts_each_plan(client):
    payload = client.list_customers()
    assert plan_breakdown(payload) == {"pro": 1, "free": 1}


def test_display_names_are_sorted_as_returned(client):
    payload = client.list_customers()
    assert display_names(payload) == ["Ada Lovelace", "Grace Hopper"]


