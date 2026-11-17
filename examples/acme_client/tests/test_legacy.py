from app.legacy import cached_customer, parse_csv_row


def test_csv_rows_still_parse():
    row = ["  Ada Lovelace ", " ada@example.com "]
    parsed = parse_csv_row(row)
    assert parsed == {
        "display_name": "Ada Lovelace",
        "contact": "ada@example.com",
        "plan": "free",
    }


def test_cached_customer_lookup():
    cache = {"customer": {"name": "Grace", "plan": "pro"}}
    assert cached_customer(cache, "customer")["display_name"] == "Grace"
