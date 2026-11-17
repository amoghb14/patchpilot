"""Test fixtures: boot the mock API in v1 or v2 mode for the whole session.

Set ``ACME_API_MODE=v2`` to run the suite against the new, breaking contract.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import mock_api  # noqa: E402

from app.client import AcmeClient  # noqa: E402

MODE = os.environ.get("ACME_API_MODE", "v1")


@pytest.fixture(scope="session", autouse=True)
def api_server():
    server, port = mock_api.start(MODE, 0)
    os.environ["ACME_BASE_URL"] = f"http://127.0.0.1:{port}"
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def client(api_server):
    return AcmeClient(api_server)
