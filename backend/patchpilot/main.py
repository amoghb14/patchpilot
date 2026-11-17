"""PatchPilot API entrypoint: ``uvicorn patchpilot.main:app --reload``."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import router
from .config import get_settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title="PatchPilot",
    version=__version__,
    description=(
        "Detects breaking changes between two OpenAPI contracts, finds the Python code "
        "that depends on them, generates a source-preserving migration and verifies it "
        "by running the repository's tests."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def index() -> dict:
    return {
        "name": "PatchPilot",
        "version": __version__,
        "docs": "/docs",
        "api": "/api/health",
    }
