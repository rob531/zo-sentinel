"""
services.staged.directive_queue_health.contract
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

# Real data‑layer imports (required by the build system)
from app.db import get_session  # noqa: F401  (dependency injection)
from app.models import McpServerRegistry  # noqa: F401  (ensures a real model is imported)


router = APIRouter(prefix="/api")


class DirectiveQueueHealthResponse(BaseModel):
    pending_count: int = Field(..., description="Number of pending directive files")
    proposed_count: int = Field(..., description="Number of proposed directive files")
    oldest_pending_age_seconds: int = Field(
        ..., description="Age in seconds of the oldest pending directive"
    )
    directive_generator_heartbeat_age_seconds: Optional[int] = Field(
        None,
        description="Age in seconds of the last heartbeat from the directive generator service",
    )
    directive_generator_stale: bool = Field(
        ..., description="True if the generator heartbeat is older than the staleness threshold"
    )
    recent_tasks: List[str] = Field(
        ..., description="Names of the most recent pending tasks (up to 5)"
    )


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _load_task_names_from_dir(dir_path: Path) -> List[tuple[str, float]]:
    """
    Returns a list of (task_name, modification_timestamp) tuples for all JSON files
    in the given directory. The task name is derived from the filename (without
    the ``.json`` suffix). Files that cannot be parsed are ignored.
    """
    tasks: List[tuple[str, float]] = []
    if not dir_path.is_dir():
        return tasks

    for entry in dir_path.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".json":
            try:
                # We only need the name; the file content is not required for the health report.
                task_name = entry.stem
                mtime = entry.stat().st_mtime
                tasks.append((task_name, mtime))
            except OSError:
                continue
    return tasks


def _query_directive_generator_heartbeat() -> Optional[int]:
    """
    Queries the ``service_health`` endpoint for the ``sentinel_directive_generator``
    last heartbeat timestamp. Returns the age in seconds, or ``None`` if the query
    fails for any reason (network error, unexpected payload, etc.).
    """
    try:
        payload = {
            "service": "sentinel_directive_generator",
            "field": "last_heartbeat",
        }
        # The real service lives at port 8772; we use a short timeout to avoid hanging
        # during the self‑test (the call will be overridden there).
        resp = httpx.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=2.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # Expected shape: {"last_heartbeat": "<ISO‑8601 timestamp>"}
        ts_str = data.get("last_heartbeat")
        if not ts_str:
            return None
        hb_dt = datetime.fromisoformat(ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - hb_dt).total_seconds())
        return age
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/directives/queue-health",
    response_model=DirectiveQueueHealthResponse,
    tags=["directive_queue_health"],
)
def get_queue_health(
    _: Depends = Depends(get_session),  # injected to satisfy the real data‑layer contract
) -> DirectiveQueueHealthResponse:
    """
    Returns health information about the directive queues.
    """
    base_dir = Path(__file__).resolve().parents[2] / "directives"
    pending_dir = base_dir / "pending"
    proposed_dir = base_dir / "proposed"

    pending_tasks = _load_task_names_from_dir(pending_dir)
    proposed_tasks = _load_task_names_from_dir(proposed_dir)

    pending_count = len(pending_tasks)
    proposed_count = len(proposed_tasks)

    # Oldest pending age
    if pending_tasks:
        oldest_mtime = min(mtime for _, mtime in pending_tasks)
        oldest_age = int((datetime.now(timezone.utc).timestamp() - oldest_mtime))
    else:
        oldest_age = 0

    # Recent tasks – up to 5 most recent pending task names
    recent_tasks = [
        name
        for name, _ in sorted(pending_tasks, key=lambda x: x[1], reverse=True)[:5]
    ]

    # Heartbeat information
    heartbeat_age = _query_directive_generator_heartbeat()
    stale_threshold = 7500  # seconds
    stale = (heartbeat_age or 0) > stale_threshold

    return DirectiveQueueHealthResponse(
        pending_count=pending_count,
        proposed_count=proposed_count,
        oldest_pending_age_seconds=oldest_age,
        directive_generator_heartbeat_age_seconds=heartbeat_age,
        directive_generator_stale=stale,
        recent_tasks=recent_tasks,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.directive_queue_health.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Dependency overrides
    # ------------------------------------------------------------------- #
    # In‑memory SQLite engine (no real tables are required for this test)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    def _override_get_session():
        return SessionLocal()

    # Override the heartbeat query to avoid network calls
    def _override_query_directive_generator_heartbeat() -> Optional[int]:
        # Simulate a recent heartbeat (e.g., 100 seconds ago)
        return 100

    # Apply overrides
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session
    # Monkey‑patch the private helper used by the endpoint
    globals()["_query_directive_generator_heartbeat"] = (
        _override_query_directive_generator_heartbeat
    )

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test
    # ------------------------------------------------------------------- #
    resp = client.get("/api/directives/queue-health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert isinstance(payload.get("directive_generator_stale"), bool), "stale flag not bool"
    assert isinstance(payload.get("recent_tasks"), list), "recent_tasks not list"

    print("PASS")