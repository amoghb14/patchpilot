"""Runtime configuration.

Everything is overridable through ``PATCHPILOT_*`` environment variables so the
same code runs against SQLite locally and PostgreSQL in a deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATCHPILOT_", env_file=".env", extra="ignore")

    # Absolute default so the API, the seeder and the CLI all share one database
    # regardless of the working directory.
    database_url: str = Field(default=f"sqlite:///{PROJECT_ROOT / 'patchpilot.db'}")
    workspace_root: Path = Field(default=Path.home() / ".patchpilot" / "repos")

    # Repositories may only be read from these roots. Guards against a request
    # pointing the analyser at /etc or another user's home directory.
    allowed_repo_roots: list[Path] = Field(default_factory=lambda: [Path.home(), Path("/tmp"), PROJECT_ROOT])

    max_repository_files: int = 3000
    max_archive_bytes: int = 40_000_000
    scan_timeout_seconds: int = 120
    test_timeout_seconds: int = 240

    github_token: str | None = Field(default=None)
    github_api_base: str = "https://api.github.com"
    github_allow_imports: bool = True

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    @property
    def resolved_allowed_roots(self) -> list[Path]:
        roots = list(self.allowed_repo_roots)
        if PROJECT_ROOT not in roots:
            roots.append(PROJECT_ROOT)
        return [Path(root).expanduser().resolve() for root in roots]

    def is_path_allowed(self, path: Path) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self.resolved_allowed_roots)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
    os.environ.pop("PATCHPILOT_DATABASE_URL", None)
