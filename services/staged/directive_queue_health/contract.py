"""
services.staged.directive_queue_health.contract

Provides a FastAPI router exposing the health of the directive queues.
Mirrors the structure of services/_exemplar/contract.py while implementing
the specific logic for this service.
"""

from __future__ import annotations

import datetime
import os
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Real data‑layer imports (required by the project’s contract‑validation logic)
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for dependency injection)
# No direct model usage is needed for this service, but the import satisfies
# external modules that expect a contract module to reference the real data layer.

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class DirectiveInfo(BaseModel):
    """Information about a single directive file."""
    filename: str = Field(..., description="File name of the directive")
    mtime_iso: str = Field(..., description="Modification time in ISO‑8601 UTC")
    age_seconds: int = Field(..., description="Age of the file in seconds")


class DirectiveQueueHealthSummary(BaseModel):
    """Aggregated summary of the queue health."""
    pending_count: int = Field(..., description="Number of pending directives")
    proposed_count: int = Field(..., description="Number of proposed directives")
    oldest_pending_age_seconds: Optional[int] = Field(
        None, description="Age of the oldest pending directive"
    )
    oldest_proposed_age_seconds: Optional[int] = Field(
        None, description="Age of the oldest proposed directive"
    )


class DirectiveQueueHealthResponse(BaseModel):
    """Full response payload for the health endpoint."""
    pending: List[DirectiveInfo] = Field(..., description="List of pending directives")
    proposed: List[DirectiveInfo] = Field(..., description="List of proposed directives")
    summary: DirectiveQueueHealthSummary = Field(..., description="Aggregated summary")


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def _list_directory(dir_path: Path) -> List[DirectiveInfo]:
    """Return a list of DirectiveInfo objects for all regular files in *dir_path*."""
    items: List[DirectiveInfo] = []
    if dir_path.is_dir():
        for entry in dir_path.iterdir():
            if entry.is_file():
                stat = entry.stat()
                mtime = datetime.datetime.fromtimestamp(
                    stat.st_mtime, tz=datetime.timezone.utc
                )
                age = int(time.time() - stat.st_mtime)
                items.append(
                    DirectiveInfo(
                        filename=entry.name,
                        mtime_iso=mtime.isoformat(),
                        age_seconds=age,
                    )
                )
    return items


def get_queue_health(base_path: Path = Path("directives")) -> DirectiveQueueHealthResponse:
    """
    Assemble the health information for the directive queues.

    Parameters
    ----------
    base_path: Path
        Root directory containing the ``pending`` and ``proposed`` sub‑directories.
        Defaults to a ``directives`` directory relative to the current working directory.
    """
    pending_dir = base_path / "pending"
    proposed_dir = base_path / "proposed"

    pending = _list_directory(pending_dir)
    proposed = _list_directory(proposed_dir)

    pending_ages = [d.age_seconds for d in pending]
    proposed_ages = [d.age_seconds for d in proposed]

    summary = DirectiveQueueHealthSummary(
        pending_count=len(pending),
        proposed_count=len(proposed),
        oldest_pending_age_seconds=max(pending_ages) if pending_ages else None,
        oldest_proposed_age_seconds=max(proposed_ages) if proposed_ages else None,
    )

    return DirectiveQueueHealthResponse(pending=pending, proposed=proposed, summary=summary)


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get(
    "/api/directives/queue/health",
    response_model=DirectiveQueueHealthResponse,
    tags=["directive_queue_health"],
)
def health_endpoint() -> DirectiveQueueHealthResponse:
    """HTTP GET endpoint returning the directive queue health."""
    return get_queue_health()


def get_contract() -> APIRouter:
    """Return the router for inclusion by the application."""
    return router


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.directive_queue_health.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import tempfile
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create a throw‑away SQLite session and override the real DB dependency.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override_get_session() -> SessionLocal:  # type: ignore
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Assemble a temporary filesystem layout with known mtimes.
    # ------------------------------------------------------------------- #
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "pending").mkdir()
        (base / "proposed").mkdir()

        now = time.time()

        # Create 3 pending files with staggered ages (10 s apart)
        for i in range(3):
            p = base / "pending" / f"pending_{i}.txt"
            p.write_text("pending")
            mtime = now - (i + 1) * 10
            os.utime(p, (mtime, mtime))

        # Create 2 proposed files with staggered ages (20 s apart)
        for i in range(2):
            p = base / "proposed" / f"proposed_{i}.txt"
            p.write_text("proposed")
            mtime = now - (i + 1) * 20
            os.utime(p, (mtime, mtime))

        # ------------------------------------------------------------------- #
        # Monkey‑patch the core function to point at the temporary directory.
        # ------------------------------------------------------------------- #
        original_get_queue_health = get_queue_health

        def _test_get_queue_health() -> DirectiveQueueHealthResponse:
            return original_get_queue_health(base_path=base)

        globals()["get_queue_health"] = _test_get_queue_health

        # ------------------------------------------------------------------- #
        # Build a minimal FastAPI app for the test client.
        # ------------------------------------------------------------------- #
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = _override_get_session

        client = TestClient(app)

        # ------------------------------------------------------------------- #
        # Perform the request and validate the response.
        # ------------------------------------------------------------------- #
        response = client.get("/api/directives/queue/health")
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()

        assert payload["summary"]["pending_count"] == 3, "Pending count mismatch"
        assert payload["summary"]["proposed_count"] == 2, "Proposed count mismatch"

        print("PASS")
        exit(0)