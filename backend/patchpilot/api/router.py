"""HTTP API surface."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import PROJECT_ROOT, get_settings
from ..db import get_db
from ..engine.analysis import scan_repository
from ..engine.migration import apply_plan_patch
from ..engine.openapi import MigrationMetadata, diff_models, normalize, summarize as summarize_diff
from ..engine.openapi.loader import SpecError
from ..integrations.github import GitHubClient, GitHubError
from ..models import (
    ApiSpec,
    Finding,
    Migration,
    PullRequestRecord,
    Repository,
    Scan,
    Verification,
)
from ..schemas import (
    DiffRequest,
    PullRequestCreate,
    RepositoryCreate,
    ScanCreate,
    SpecCreate,
    VerifyRequest,
)
from ..services import build_migration, create_scan, latest_verification, parse_test_command, run_verification

router = APIRouter(prefix="/api")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}


# -- serializers ---------------------------------------------------------------
def repository_dict(repo: Repository) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "source_type": repo.source_type,
        "source_ref": repo.source_ref,
        "root_path": repo.root_path,
        "file_count": repo.file_count,
        "default_branch": repo.default_branch,
        "created_at": repo.created_at.isoformat() if repo.created_at else None,
    }


def spec_dict(spec: ApiSpec) -> dict:
    return {
        "id": spec.id,
        "name": spec.name,
        "version_label": spec.version_label,
        "title": spec.title,
        "content_hash": spec.content_hash,
        "operation_count": spec.operation_count,
        "field_count": spec.field_count,
        "operation_keys": spec.operation_keys,
        "created_at": spec.created_at.isoformat() if spec.created_at else None,
    }


def location_dict(row) -> dict:
    return {
        "file": row.file,
        "line": row.line,
        "col": row.col,
        "expression": row.expression,
        "function": row.function,
        "access_kind": row.access_kind,
        "field": row.field,
        "path": row.path,
    }


def finding_dict(finding: Finding) -> dict:
    payload = dict(finding.change_json or {})
    payload.update(
        {
            "id": finding.id,
            "affected_count": finding.affected_count,
            "locations": [location_dict(location) for location in finding.locations],
        }
    )
    return payload


def migration_dict(migration: Migration, verification: Verification | None = None) -> dict:
    return {
        "id": migration.id,
        "scan_id": migration.scan_id,
        "status": migration.status,
        "edits": migration.edits,
        "skipped": migration.skipped,
        "diff": migration.diff,
        "stats": migration.stats,
        "files": sorted((migration.files or {}).keys()),
        "created_at": migration.created_at.isoformat() if migration.created_at else None,
        "verification": verification_dict(verification) if verification else None,
    }


def verification_dict(verification: Verification) -> dict:
    return {
        "id": verification.id,
        "verdict": verification.verdict,
        "summary": verification.summary,
        "command": verification.command,
        "baseline": verification.baseline,
        "patched": verification.patched,
        "created_at": verification.created_at.isoformat() if verification.created_at else None,
    }


def scan_summary(scan: Scan, repo: Repository | None = None) -> dict:
    return {
        "id": scan.id,
        "repository_id": scan.repository_id,
        "repository_name": repo.name if repo else None,
        "status": scan.status,
        "stats": scan.stats,
        "duration_ms": scan.duration_ms,
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
    }


# -- helpers -------------------------------------------------------------------
def _get_or_404(db: Session, model, identifier: str, label: str):
    entity = db.get(model, identifier)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return entity


def _count_python_files(root: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        total += sum(1 for name in filenames if name.endswith(".py"))
    return total


def _validate_local_path(raw: str) -> Path:
    settings = get_settings()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="repository path must be absolute")
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="repository path does not exist")
    if not settings.is_path_allowed(path):
        raise HTTPException(
            status_code=403,
            detail="repository path is outside the allowed roots for this server",
        )
    return path.resolve()


# -- meta ---------------------------------------------------------------------
@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {
        "status": "ok",
        "version": __version__,
        "database": get_settings().database_url.split("://", 1)[0],
        "engine": {
            "openapi": "3.x",
            "language": "python",
            "migration_rules": ["response field rename", "removed field", "required request field", "endpoint move"],
        },
    }


# -- repositories --------------------------------------------------------------
@router.post("/repositories", status_code=201)
def create_repository(body: RepositoryCreate, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if body.source_type == "local":
        path = _validate_local_path(body.source)
        repo = Repository(
            id=str(__import__("uuid").uuid4()),
            name=body.name or path.name,
            source_type="local",
            source_ref=str(path),
            root_path=str(path),
            file_count=_count_python_files(path),
            default_branch=body.branch or "main",
        )
    else:
        if not settings.github_allow_imports:
            raise HTTPException(status_code=403, detail="GitHub imports are disabled on this server")
        import uuid

        identifier = str(uuid.uuid4())
        destination = settings.workspace_root / identifier
        client = GitHubClient(token=settings.github_token, api_base=settings.github_api_base)
        try:
            result = client.download_repository(
                body.source,
                destination,
                ref=body.branch,
                max_bytes=settings.max_archive_bytes,
                max_files=settings.max_repository_files,
            )
        except GitHubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        repo = Repository(
            id=identifier,
            name=body.name or body.source,
            source_type="github",
            source_ref=body.source,
            root_path=result["path"],
            file_count=_count_python_files(Path(result["path"])),
            default_branch=body.branch or "main",
        )
    db.add(repo)
    db.commit()
    return repository_dict(repo)


@router.get("/repositories")
def list_repositories(db: Session = Depends(get_db)) -> list[dict]:
    repos = db.execute(select(Repository).order_by(Repository.created_at.desc())).scalars().all()
    return [repository_dict(repo) for repo in repos]


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: str, db: Session = Depends(get_db)) -> dict:
    repo = _get_or_404(db, Repository, repository_id, "repository")
    scans = db.execute(
        select(Scan).where(Scan.repository_id == repo.id).order_by(Scan.created_at.desc()).limit(20)
    ).scalars().all()
    payload = repository_dict(repo)
    payload["scans"] = [scan_summary(scan, repo) for scan in scans]
    return payload


# -- specs ---------------------------------------------------------------------
@router.post("/specs", status_code=201)
def create_spec(body: SpecCreate, db: Session = Depends(get_db)) -> dict:
    try:
        model = normalize(body.content)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = db.execute(
        select(ApiSpec).where(ApiSpec.content_hash == model.source_hash, ApiSpec.name == body.name)
    ).scalars().first()
    if existing:
        return spec_dict(existing)
    spec = ApiSpec(
        id=str(__import__("uuid").uuid4()),
        name=body.name,
        version_label=body.version_label or model.version,
        content_hash=model.source_hash,
        raw=body.content,
        title=model.title,
        operation_count=model.operation_count,
        field_count=model.field_count,
        operation_keys=model.endpoint_keys(),
    )
    db.add(spec)
    db.commit()
    return spec_dict(spec)


@router.get("/specs")
def list_specs(db: Session = Depends(get_db)) -> list[dict]:
    specs = db.execute(select(ApiSpec).order_by(ApiSpec.created_at.desc())).scalars().all()
    return [spec_dict(spec) for spec in specs]


@router.get("/specs/{spec_id}")
def get_spec(spec_id: str, db: Session = Depends(get_db)) -> dict:
    spec = _get_or_404(db, ApiSpec, spec_id, "spec")
    payload = spec_dict(spec)
    payload["raw"] = spec.raw
    return payload


# -- diff (stateless) ----------------------------------------------------------
@router.post("/diffs")
def diff_specs(body: DiffRequest) -> dict:
    try:
        old_model = normalize(body.old_spec)
        new_model = normalize(body.new_spec)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata = MigrationMetadata.from_dict(body.metadata)
    changes = diff_models(old_model, new_model, metadata)
    return {
        "summary": summarize_diff(changes),
        "changes": [change.as_dict() for change in changes],
        "specs": {
            "old": {"title": old_model.title, "version": old_model.version, "operations": old_model.operation_count},
            "new": {"title": new_model.title, "version": new_model.version, "operations": new_model.operation_count},
        },
    }


# -- scans ---------------------------------------------------------------------
@router.post("/scans", status_code=201)
def create_scan_endpoint(body: ScanCreate, db: Session = Depends(get_db)) -> dict:
    repo = _get_or_404(db, Repository, body.repository_id, "repository")
    old_spec = _get_or_404(db, ApiSpec, body.old_spec_id, "spec")
    new_spec = _get_or_404(db, ApiSpec, body.new_spec_id, "spec")
    if old_spec.id == new_spec.id:
        raise HTTPException(status_code=422, detail="old and new specs must differ")
    try:
        scan = create_scan(db, repo, old_spec, new_spec, body.metadata)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    findings = db.execute(select(Finding).where(Finding.scan_id == scan.id)).scalars().all()
    payload = scan_summary(scan, repo)
    payload["findings"] = [finding_dict(finding) for finding in findings]
    return payload


@router.get("/scans")
def list_scans(repository_id: str | None = Query(default=None), db: Session = Depends(get_db)) -> list[dict]:
    query = select(Scan).order_by(Scan.created_at.desc()).limit(50)
    if repository_id:
        query = query.where(Scan.repository_id == repository_id)
    scans = db.execute(query).scalars().all()
    return [scan_summary(scan, db.get(Repository, scan.repository_id)) for scan in scans]


@router.get("/scans/{scan_id}")
def get_scan(scan_id: str, db: Session = Depends(get_db)) -> dict:
    scan = _get_or_404(db, Scan, scan_id, "scan")
    repo = db.get(Repository, scan.repository_id)
    old_spec = db.get(ApiSpec, scan.old_spec_id)
    new_spec = db.get(ApiSpec, scan.new_spec_id)
    findings = db.execute(select(Finding).where(Finding.scan_id == scan.id)).scalars().all()
    migrations = db.execute(
        select(Migration).where(Migration.scan_id == scan.id).order_by(Migration.created_at.desc())
    ).scalars().all()
    payload = scan_summary(scan, repo)
    payload.update(
        {
            "repository": repository_dict(repo) if repo else None,
            "old_spec": spec_dict(old_spec) if old_spec else None,
            "new_spec": spec_dict(new_spec) if new_spec else None,
            "metadata": scan.scan_metadata,
            "findings": [finding_dict(finding) for finding in findings],
            "migrations": [
                {
                    "id": migration.id,
                    "status": migration.status,
                    "stats": migration.stats,
                    "created_at": migration.created_at.isoformat() if migration.created_at else None,
                }
                for migration in migrations
            ],
        }
    )
    return payload


# -- migration -----------------------------------------------------------------
@router.post("/scans/{scan_id}/migration", status_code=201)
def create_migration(scan_id: str, db: Session = Depends(get_db)) -> dict:
    scan = _get_or_404(db, Scan, scan_id, "scan")
    repo = _get_or_404(db, Repository, scan.repository_id, "repository")
    try:
        migration = build_migration(db, scan, repo)
    except Exception as exc:  # pragma: no cover - defensive, surfaced to the UI
        raise HTTPException(status_code=500, detail=f"migration generation failed: {exc}") from exc
    return migration_dict(migration)


@router.get("/migrations/{migration_id}")
def get_migration(migration_id: str, db: Session = Depends(get_db)) -> dict:
    migration = _get_or_404(db, Migration, migration_id, "migration")
    return migration_dict(migration, latest_verification(db, migration.id))


@router.post("/migrations/{migration_id}/verify", status_code=201)
def verify_migration_endpoint(
    migration_id: str, body: VerifyRequest, db: Session = Depends(get_db)
) -> dict:
    migration = _get_or_404(db, Migration, migration_id, "migration")
    scan = _get_or_404(db, Scan, migration.scan_id, "scan")
    repo = _get_or_404(db, Repository, scan.repository_id, "repository")
    try:
        command = parse_test_command(body.test_command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    verification = run_verification(db, migration, repo, command, body.env, body.timeout_seconds)
    return verification_dict(verification)


@router.post("/migrations/{migration_id}/pull-request", status_code=201)
def create_pull_request_endpoint(
    migration_id: str, body: PullRequestCreate, db: Session = Depends(get_db)
) -> dict:
    migration = _get_or_404(db, Migration, migration_id, "migration")
    if not migration.files:
        raise HTTPException(status_code=422, detail="migration contains no file changes")
    verification = latest_verification(db, migration.id)
    if verification is None or verification.verdict != "verified":
        raise HTTPException(
            status_code=409,
            detail="run verification first: a pull request is only opened for a verified migration",
        )
    settings = get_settings()
    branch = body.branch_name or f"patchpilot/migrate-{migration.id[:8]}"
    body_text = body.body or (
        f"Automated migration generated by PatchPilot.\n\n"
        f"Verification: {verification.summary}\n\n"
        f"```diff\n{migration.diff[:20000]}\n```\n"
    )
    client = GitHubClient(token=settings.github_token, api_base=settings.github_api_base)
    try:
        result = client.create_pull_request(
            body.repo_full_name,
            branch,
            body.base_branch,
            migration.files,
            body.title,
            body_text,
            "Apply PatchPilot API migration",
        )
    except GitHubError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = PullRequestRecord(
        id=str(__import__("uuid").uuid4()),
        migration_id=migration.id,
        repo_full_name=body.repo_full_name,
        branch=result["branch"],
        base_branch=body.base_branch,
        number=result.get("number"),
        url=result.get("url"),
        status="created",
    )
    migration.status = "pull_request_open"
    db.add(record)
    db.commit()
    return {
        "id": record.id,
        "number": record.number,
        "url": record.url,
        "branch": record.branch,
        "base_branch": record.base_branch,
        "status": record.status,
    }


# -- benchmark artifact --------------------------------------------------------
@router.get("/benchmarks/latest")
def latest_benchmark() -> dict:
    path = PROJECT_ROOT / "benchmarks" / "latest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no benchmark has been run yet (see scripts/benchmark.py)")
    return json.loads(path.read_text(encoding="utf-8"))
