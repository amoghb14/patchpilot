"""Reporting helpers built on top of the Acme client."""

from .client import AcmeClient


def customer_summary(customer: dict) -> dict:
    email = customer["email"]
    return {
        "display_name": customer["name"].strip(),
        "contact": email if email else "no-email",
        "plan": customer["plan"],
    }


def plan_breakdown(payload: dict) -> dict:
    counts: dict[str, int] = {}
    for customer in payload["data"]:
        plan = customer["plan"]
        counts[plan] = counts.get(plan, 0) + 1
    return counts


def display_names(payload: dict) -> list[str]:
    return [customer["name"] for customer in payload["data"]]


def render_customer_page(client: AcmeClient, customer_id: str) -> dict:
    return customer_summary(client.get_customer(customer_id))
