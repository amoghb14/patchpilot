"""Plan upgrade flow."""

from .client import AcmeClient


def upgrade_to_pro(client: AcmeClient, customer_id: str) -> dict:
    return client.update_plan(customer_id, "pro")


def churn(client: AcmeClient, customer_id: str) -> None:
    client.delete_customer(customer_id)
