"""Thin HTTP client for the Acme Platform API."""

import requests

from . import config


class AcmeClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or config.base_url()).rstrip("/")
        self.session = requests.Session()

    def get_customer(self, customer_id: str) -> dict:
        response = self.session.get(f"{self.base_url}/v1/customers/{customer_id}")
        response.raise_for_status()
        return response.json()

    def list_customers(self, limit: int = 10) -> dict:
        response = self.session.get(f"{self.base_url}/v1/customers", params={"limit": limit})
        response.raise_for_status()
        return response.json()

    def update_plan(self, customer_id: str, plan: str) -> dict:
        response = self.session.patch(
            f"{self.base_url}/v1/customers/{customer_id}",
            json={"plan": plan},
        )
        response.raise_for_status()
        return response.json()

    def get_invoice(self, invoice_id: str) -> dict:
        response = self.session.get(f"{self.base_url}/v1/invoices/{invoice_id}")
        response.raise_for_status()
        return response.json()

    def delete_customer(self, customer_id: str) -> None:
        response = self.session.delete(f"{self.base_url}/v1/customers/{customer_id}")
        response.raise_for_status()
