"""Seed the local database with the demo repository, scan, migration and verification.

    python scripts/seed_demo.py

Runs entirely in-process (no API server needed) and is safe to run twice.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from patchpilot.db import SessionLocal, init_db  # noqa: E402
from patchpilot.models import ApiSpec, PullRequestRecord, Repository, Scan  # noqa: E402
from patchpilot.services import build_migration, create_scan, run_verification  # noqa: E402
from patchpilot.engine.openapi import normalize  # noqa: E402

EXAMPLE = PROJECT_ROOT / "examples" / "acme_client"
FIXTURES = PROJECT_ROOT / "fixtures"


def reset_demo(session) -> None:
    """Remove a previous demo run so repeated seeding does not pile up scans."""
    for repo in session.execute(select(Repository).where(Repository.name == "acme-client")).scalars().all():
        migration_ids = [migration.id for scan in repo.scans for migration in scan.migrations]
        if migration_ids:
            session.execute(
                delete(PullRequestRecord).where(PullRequestRecord.migration_id.in_(migration_ids))
            )
        for scan in list(repo.scans):
            for migration in list(scan.migrations):
                session.delete(migration)
            session.delete(scan)
        session.delete(repo)
    session.flush()


def main() -> int:
    init_db()
    session = SessionLocal()
    try:
        reset_demo(session)
        repo_path = str(EXAMPLE)
        repo = Repository(
            id=str(uuid.uuid4()),
            name="acme-client",
            source_type="local",
            source_ref=repo_path,
            root_path=repo_path,
            file_count=len(list(EXAMPLE.rglob("*.py"))),
        )
        session.add(repo)

        specs = []
        for label, filename in (("1.4.0", "acme_v1.yaml"), ("2.0.0", "acme_v2.yaml")):
            content = (FIXTURES / filename).read_text()
            model = normalize(content)
            spec = ApiSpec(
                id=str(uuid.uuid4()),
                name="acme",
                version_label=label,
                content_hash=model.source_hash,
                raw=content,
                title=model.title,
                operation_count=model.operation_count,
                field_count=model.field_count,
                operation_keys=model.endpoint_keys(),
            )
            session.add(spec)
            specs.append(spec)
        session.commit()

        metadata = json.loads((FIXTURES / "migration_metadata.json").read_text())
        scan = create_scan(session, repo, specs[0], specs[1], metadata)
        migration = build_migration(session, scan, repo)
        verification = run_verification(
            session,
            migration,
            repo,
            [sys.executable, "-m", "pytest", "-q"],
            {"ACME_API_MODE": "v2"},
            180,
        )

        print(f"repository   {repo.name} ({repo.file_count} python files)")
        print(f"scan         {scan.id}")
        print(f"  changes    {scan.stats['diff']['total']} total, {scan.stats['diff']['by_severity']['breaking']} breaking")
        print(f"  affected   {scan.stats['matched']['affected_locations']} code locations")
        print(f"migration    {migration.id} ({migration.stats['edits']} edits, {migration.stats['files_changed']} files)")
        print(f"verification {verification.verdict}: {verification.summary}")
        print()
        print("Open the dashboard and use this scan id:")
        print(f"  {scan.id}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
