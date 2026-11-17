"""A dependency-free mock of the Acme Platform API.

Run as ``python mock_api.py --mode v2`` or import :func:`start` from the tests.
The v2 behaviour is genuinely incompatible: it drops ``email``, renames
``name``/``data``, requires ``change_reason``, and only serves invoices on /v2.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CUSTOMERS_V1 = [
    {
        "id": "cus_1",
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "plan": "pro",
        "created_at": "2024-01-04T10:00:00Z",
        "tier": 2,
    },
    {
        "id": "cus_2",
        "name": "Grace Hopper",
        "email": "grace@example.com",
        "plan": "free",
        "created_at": "2024-03-11T08:30:00Z",
        "tier": 1,
    },
]

CUSTOMERS_V2 = [
    {
        "id": "cus_1",
        "full_name": "Ada Lovelace",
        "plan": "pro",
        "created_at": 1704362400,
        "tier": 2,
    },
    {
        "id": "cus_2",
        "full_name": "Grace Hopper",
        "plan": "free",
        "created_at": 1710145800,
        "tier": 1,
    },
]

INVOICES = {
    "inv_1": {"id": "inv_1", "customer_id": "cus_1", "amount_cents": 1999, "status": "paid", "currency": "usd"},
    "inv_2": {"id": "inv_2", "customer_id": "cus_2", "amount_cents": 0, "status": "draft", "currency": "eur"},
}

STATE = {"mode": os.environ.get("ACME_API_MODE", "v1")}
PLAN_OVERRIDES: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    server_version = "AcmeMock/1.0"

    def log_message(self, fmt: str, *args) -> None:  # keep pytest output clean
        pass

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, payload: dict | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _customers(self) -> list[dict]:
        source = CUSTOMERS_V1 if STATE["mode"] == "v1" else CUSTOMERS_V2
        out = []
        for customer in source:
            clone = dict(customer)
            if clone["id"] in PLAN_OVERRIDES:
                clone["plan" if STATE["mode"] == "v1" else "plan"] = PLAN_OVERRIDES[clone["id"]]
            out.append(clone)
        return out

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            return None

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        if parts == ["v1", "customers"]:
            customers = self._customers()
            if STATE["mode"] == "v1":
                return self._send(200, {"data": customers, "total": len(customers)})
            return self._send(200, {"results": customers, "total": len(customers), "has_more": False})
        if len(parts) == 3 and parts[:2] == ["v1", "customers"]:
            for customer in self._customers():
                if customer["id"] == parts[2]:
                    return self._send(200, customer)
            return self._send(404, {"error": "customer_not_found"})
        invoice_prefix = "v2" if STATE["mode"] == "v2" else "v1"
        if len(parts) == 3 and parts[:2] == [invoice_prefix, "invoices"]:
            invoice = INVOICES.get(parts[2])
            if invoice is None:
                return self._send(404, {"error": "invoice_not_found"})
            payload = dict(invoice)
            if STATE["mode"] == "v2" and payload["status"] == "draft":
                payload["status"] = "open"
            return self._send(200, payload)
        return self._send(404, {"error": "not_found", "path": path})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        if len(parts) != 3 or parts[:2] != ["v1", "customers"]:
            return self._send(404, {"error": "not_found"})
        body = self._read_json() or {}
        if STATE["mode"] == "v2" and "change_reason" not in body:
            # New in v2: the audit log requires a reason for every plan change.
            return self._send(422, {"error": "change_reason_required"})
        customer_id = parts[2]
        PLAN_OVERRIDES[customer_id] = body.get("plan", "pro")
        for customer in self._customers():
            if customer["id"] == customer_id:
                return self._send(200, customer)
        return self._send(404, {"error": "customer_not_found"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if STATE["mode"] == "v2":
            # DELETE /v1/customers/{id} was removed from the v2 contract.
            return self._send(404, {"error": "not_found"})
        return self._send(204)


def start(mode: str | None = None, port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    if mode:
        STATE["mode"] = mode
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["v1", "v2"], default=os.environ.get("ACME_API_MODE", "v1"))
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    server, port = start(args.mode, args.port)
    print(f"Acme mock API ({args.mode}) on http://127.0.0.1:{port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
