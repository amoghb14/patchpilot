"""Request bodies accepted by the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RepositoryCreate(BaseModel):
    name: str | None = None
    source_type: Literal["local", "github"] = "local"
    source: str = Field(description="Absolute path for local, 'owner/repo' for github")
    branch: str | None = None

    @field_validator("source")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class SpecCreate(BaseModel):
    name: str
    content: str
    version_label: str = ""


class ScanCreate(BaseModel):
    repository_id: str
    old_spec_id: str
    new_spec_id: str
    metadata: dict | None = None


class DiffRequest(BaseModel):
    old_spec: str
    new_spec: str
    metadata: dict | None = None


class VerifyRequest(BaseModel):
    test_command: str | None = Field(
        default=None,
        description="Defaults to 'python -m pytest -q'. Shell metacharacters are rejected.",
    )
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=180, ge=10, le=900)


class PullRequestCreate(BaseModel):
    repo_full_name: str
    base_branch: str = "main"
    branch_name: str | None = None
    title: str = "PatchPilot: migrate API client to the new contract"
    body: str | None = None
