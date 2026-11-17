"""Invoice helpers."""

from .client import AcmeClient


def invoice_total_cents(invoice: dict) -> int:
    if invoice["status"] == "draft":
        return 0
    return invoice["amount_cents"]


def invoice_label(invoice: dict) -> str:
    return f"{invoice['currency'].upper()} {invoice['amount_cents'] / 100:.2f}"


def fetched_invoice_total(client: AcmeClient, invoice_id: str) -> int:
    return invoice_total_cents(client.get_invoice(invoice_id))
