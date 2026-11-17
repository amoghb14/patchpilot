import os


def base_url() -> str:
    """Read the API origin at call time so tests can inject a local server."""
    return os.environ.get("ACME_BASE_URL", "http://127.0.0.1:8799").rstrip("/")
