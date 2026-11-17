import os
import tempfile
from pathlib import Path

_DB_DIR = tempfile.mkdtemp(prefix="patchpilot-test-db-")
os.environ.setdefault("PATCHPILOT_DATABASE_URL", f"sqlite:///{Path(_DB_DIR) / 'test.db'}")

import pytest  # noqa: E402

from patchpilot.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()
    yield
