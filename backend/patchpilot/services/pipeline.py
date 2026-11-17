"""Scan → migration → verification orchestration.

Routes stay thin; all multi-step engine work lives here so it can be tested
without HTTP and reused by the CLI/benchmark scripts.
"""

from __future__ import annotations

import difflib
import json
import shlex
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..engine.analysis import match_changes, scan_repository, summarise as summarise_code
from ..engine.migration import apply_plan_patch, plan_patch
from ..engine.openapi import MigrationMetadata, diff_models, normalize, summarize as summarize_diff
from ..engine.openapi.differ import Change
from ..engine.analysis.matcher import Location, MatchedChange
from ..engine.migration.planner import CodeEdit
from ..engine.verification import verify_patch
from ..models import ApiSpec, Finding, FindingLocation, Migration, Repository, Scan, Verification

ALLOWED_EXECUTABLES = {"python", "python3", "pytest", "pytest3", "npm", "npx", "bun", "node"}
SHELL_METACHARACTERS = (";", "|", "&", "$", "`", ">", "<", "\n")


def new_id() -> str:
    return str(uuid.uuid4())


def parse_test_command(text: str | None) -> list[str]:
    """Turn a human-supplied test command into an argv list, safely."""
    if not text:
        return [sys.executable, "-m", "pytest", "-q"]
    if any(token in text for token in SHELL_METACHARACTERS):
        raise ValueError("test_command must not contain shell metacharacters")
    parts = shlex.split(text)
    if not parts:
        raise ValueError("test_command is empty")
    executable = Path(parts[0]).name
    if executable not in ALLOWED_EXECUTABLES:
        raise ValueError(f"executable '{executable}' is not allowed for verification")
    if executable in {"python", "python3"}:
        parts[0] = sys.executable
    return parts


def unified_diff(original: str, patched: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


# -- scan ---------------------------------------------------------------------
def create_scan(
    db: Session,
    repository: Repository,
    old_spec: ApiSpec,
    new_spec: ApiSpec,
    metadata: dict | None = None,
) -> Scan:
    started = time.perf_counter()
    old_model = normalize(old_spec.raw)
    new_model = normalize(new_spec.raw)
    rules = MigrationMetadata.from_dict(metadata)
    changes = diff_models(old_model, new_model, rules)

    root = Path(repository.root_path)
    code = scan_repository(root, old_model)
    matched = match_changes(code, changes)

    scan = Scan(
        id=new_id(),
        repository_id=repository.id,
        old_spec_id=old_spec.id,
        new_spec_id=new_spec.id,
        status="complete",
        scan_metadata=metadata or {},
        stats={},
        duration_ms=0,
    )
    db.add(scan)
    db.flush()

    for item in matched:
        change = item.change
        finding = Finding(
            id=new_id(),
            scan_id=scan.id,
            change_key=change.id,
            kind=change.kind,
            severity=change.severity,
            method=change.method,
            path=change.path,
            location=change.location,
            field_path=change.field_path,
            message=change.message,
            auto_fixable=change.auto_fixable,
            confidence=change.confidence,
            requires_review=change.requires_review,
            affected_count=len(item.locations),
            change_json={k: v for k, v in change.as_dict().items() if k != "endpoint"},
        )
        db.add(finding)
        db.flush()
        for location in item.locations:
            db.add(
                FindingLocation(
                    id=new_id(),
                    finding_id=finding.id,
                    file=location.file,
                    line=location.line,
                    col=location.col,
                    expression=location.expression,
                    function=location.function,
                    access_kind=location.access_kind,
                    field=location.field,
                    path=location.path,
                    expr_line=location.expr_line,
                    expr_col=location.expr_col,
                    site_json={
                        "url_literals": location.url_literals,
                        "json_dict_pos": list(location.json_dict_pos) if location.json_dict_pos else None,
                    },
                )
            )

    scan.duration_ms = int((time.perf_counter() - started) * 1000)
    scan.stats = {
        "diff": summarize_diff(changes),
        "code": {
            "files_analyzed": code.files_analyzed,
            "lines_scanned": code.lines_scanned,
            "call_sites": len(code.call_sites),
            "field_accesses": len(code.accesses),
            "unmapped_calls": len(code.unmapped_calls),
            "syntax_errors": code.syntax_errors,
            "duration_ms": code.duration_ms,
        },
        "matched": summarise_code(matched),
        "specs": {
            "old": {"operations": old_model.operation_count, "fields": old_model.field_count, "version": old_model.version},
            "new": {"operations": new_model.operation_count, "fields": new_model.field_count, "version": new_model.version},
        },
    }
    db.commit()
    return scan


# -- migration ----------------------------------------------------------------
def _reconstruct_matched(scan: Scan, findings: list[Finding]) -> list[MatchedChange]:
    matched: list[MatchedChange] = []
    for finding in findings:
        change = Change(**json.loads(json.dumps(finding.change_json)))
        locations: list[Location] = []
        for row in finding.locations:
            site = row.site_json or {}
            position = site.get("json_dict_pos")
            locations.append(
                Location(
                    file=row.file,
                    line=row.line,
                    col=row.col,
                    expression=row.expression,
                    function=row.function,
                    access_kind=row.access_kind,
                    field=row.field,
                    path=row.path,
                    expr_line=row.expr_line,
                    expr_col=row.expr_col,
                    url_literals=site.get("url_literals") or [],
                    json_dict_pos=tuple(position) if position else None,
                )
            )
        matched.append(MatchedChange(change=change, locations=locations))
    return matched


def build_migration(db: Session, scan: Scan, repository: Repository) -> Migration:
    findings = db.execute(select(Finding).where(Finding.scan_id == scan.id)).scalars().all()
    for finding in findings:
        _ = finding.locations  # eager-ish access inside the session

    matched = _reconstruct_matched(scan, findings)
    plan = plan_patch(matched)
    root = Path(repository.root_path)

    sources: dict[str, str] = {}
    for rel in plan.by_file:
        path = root / rel
        if path.is_file():
            sources[rel] = path.read_text(encoding="utf-8")

    results = apply_plan_patch(sources, plan.by_file)
    patched: dict[str, str] = {rel: result.new_source for rel, result in results.items() if result.changed}
    diff = "".join(unified_diff(sources[rel], patched[rel], rel) for rel in sorted(patched))
    failed = [
        {"file": rel, "edit": failure.edit.id, "detail": failure.detail}
        for rel, result in results.items()
        for failure in result.failures
    ]

    migration = Migration(
        id=new_id(),
        scan_id=scan.id,
        status="empty" if not plan.edits else "proposed",
        edits=[edit.as_dict() for edit in plan.edits],
        skipped=[skip.as_dict() for skip in plan.skipped],
        files=patched,
        diff=diff,
        stats={
            "edits": len(plan.edits),
            "edits_applied": sum(len(result.applied) for result in results.values()),
            "files_changed": len(patched),
            "skipped": len(plan.skipped),
            "failed": failed,
            "lines_added": sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")),
            "lines_removed": sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")),
        },
    )
    db.add(migration)
    db.commit()
    return migration


# -- verification -------------------------------------------------------------
def run_verification(
    db: Session,
    migration: Migration,
    repository: Repository,
    command: list[str],
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> Verification:
    settings = get_settings()
    report = verify_patch(
        Path(repository.root_path),
        migration.files,
        command,
        env or {},
        timeout or settings.test_timeout_seconds,
    )
    verification = Verification(
        id=new_id(),
        migration_id=migration.id,
        verdict=report.verdict,
        summary=report.summary,
        command=command,
        baseline=report.baseline.as_dict(),
        patched=report.patched.as_dict(),
    )
    migration.status = "verified" if report.verdict == "verified" else "needs_attention"
    db.add(verification)
    db.commit()
    return verification


def latest_verification(db: Session, migration_id: str) -> Verification | None:
    return db.execute(
        select(Verification).where(Verification.migration_id == migration_id).order_by(Verification.created_at.desc())
    ).scalars().first()


def plan_edits_from_migration(migration: Migration) -> list[CodeEdit]:
    return [CodeEdit(**item) for item in (migration.edits or [])]
