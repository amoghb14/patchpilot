"""SQLAlchemy models.

The schema mirrors the pipeline: a scan produces findings (a contract change)
with located code, a migration produces a patch, a verification proves the patch
against the test suite, and a pull request publishes it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(20))  # local | github
    source_ref: Mapped[str] = mapped_column(Text)
    root_path: Mapped[str] = mapped_column(Text)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    default_branch: Mapped[str] = mapped_column(String(120), default="main")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scans: Mapped[list["Scan"]] = relationship(back_populates="repository", cascade="all, delete-orphan")


class ApiSpec(Base):
    __tablename__ = "api_specs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version_label: Mapped[str] = mapped_column(String(80), default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200), default="")
    operation_count: Mapped[int] = mapped_column(Integer, default=0)
    field_count: Mapped[int] = mapped_column(Integer, default=0)
    operation_keys: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    old_spec_id: Mapped[str] = mapped_column(ForeignKey("api_specs.id"))
    new_spec_id: Mapped[str] = mapped_column(ForeignKey("api_specs.id"))
    status: Mapped[str] = mapped_column(String(20), default="complete")
    scan_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="scans")
    findings: Mapped[list["Finding"]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    migrations: Mapped[list["Migration"]] = relationship(back_populates="scan", cascade="all, delete-orphan")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_id: Mapped[str] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    change_key: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    location: Mapped[str] = mapped_column(String(30))
    field_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str] = mapped_column(String(20), default="n/a")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True)
    affected_count: Mapped[int] = mapped_column(Integer, default=0)
    change_json: Mapped[dict] = mapped_column(JSON, default=dict)

    scan: Mapped[Scan] = relationship(back_populates="findings")
    locations: Mapped[list["FindingLocation"]] = relationship(back_populates="finding", cascade="all, delete-orphan")


class FindingLocation(Base):
    __tablename__ = "finding_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[int] = mapped_column(Integer)
    col: Mapped[int] = mapped_column(Integer)
    expression: Mapped[str] = mapped_column(Text, default="")
    function: Mapped[str] = mapped_column(String(200), default="")
    access_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    field: Mapped[str | None] = mapped_column(String(200), nullable=True)
    path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expr_line: Mapped[int] = mapped_column(Integer, default=0)
    expr_col: Mapped[int] = mapped_column(Integer, default=0)
    site_json: Mapped[dict] = mapped_column(JSON, default=dict)

    finding: Mapped[Finding] = relationship(back_populates="locations")


class Migration(Base):
    __tablename__ = "migrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_id: Mapped[str] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    edits: Mapped[list] = mapped_column(JSON, default=list)
    skipped: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    diff: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scan: Mapped[Scan] = relationship(back_populates="migrations")
    verifications: Mapped[list["Verification"]] = relationship(
        back_populates="migration", cascade="all, delete-orphan"
    )


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    migration_id: Mapped[str] = mapped_column(ForeignKey("migrations.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text, default="")
    command: Mapped[list] = mapped_column(JSON, default=list)
    baseline: Mapped[dict] = mapped_column(JSON, default=dict)
    patched: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    migration: Mapped[Migration] = relationship(back_populates="verifications")


class PullRequestRecord(Base):
    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    migration_id: Mapped[str] = mapped_column(ForeignKey("migrations.id", ondelete="CASCADE"), index=True)
    repo_full_name: Mapped[str] = mapped_column(String(200))
    branch: Mapped[str] = mapped_column(String(200))
    base_branch: Mapped[str] = mapped_column(String(200), default="main")
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="created")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
