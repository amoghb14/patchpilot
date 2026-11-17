from app.invoices import fetched_invoice_total, invoice_label, invoice_total_cents


def test_fetched_invoice_total(client):
    assert fetched_invoice_total(client, "inv_1") == 1999


def test_draft_invoices_are_zero():
    draft = {"id": "inv_x", "amount_cents": 500, "status": "draft", "currency": "usd"}
    assert invoice_total_cents(draft) == 0


def test_invoice_label_formats_currency():
    paid = {"id": "inv_1", "amount_cents": 1999, "status": "paid", "currency": "usd"}
    assert invoice_label(paid) == "USD 19.99"
