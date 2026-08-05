"""directive_queue_health service contract."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import pytest
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_PREFIX = os.environ.get("SERVICE_PREFIX", "/api")
SERVICE_HOST = os.environ.get("SERVICE_HOST", "127.0.0.1")
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "0"))  # 0 = random

MESH_QUERY_URL = "http://127.0.0.1:8772/query"
INTERNAL_ERROR_MSG = "Internal service error"


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────


class HandlerQueueStatus(BaseModel):
    handler: str = Field(..., description="Handler name")
    pending: int = Field(..., description="Number of pending directives")
    proposed: int = Field(..., description="Number of proposed directives")
    oldest_age_seconds: int = Field(
        ..., ge=0, description="Age of oldest item in seconds"
    )


class QueueHealthSummary(BaseModel):
    total_pending: int = Field(..., ge=0)
    total_proposed: int = Field(..., ge=0)
    oldest_overall_seconds: int = Field(..., ge=0)


class QueueHealthResponse(BaseModel):
    handlers: list[HandlerQueueStatus] = Field(default_factory=list)
    summary: QueueHealthSummary


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="directive_queue_health",
    version="0.1.0",
    description="Report per-handler directive queue backlog and oldest item age.",
)


@app.get(
    f"{SERVICE_PREFIX}/directives/queue-health",
    response_model=QueueHealthResponse,
    tags=["directives"],
    summary="Queue health",
)
async def queue_health() -> QueueHealthResponse:
    """
    Query mesh_memory for directive queue metadata and compute per-handler
    backlog depth and oldest-item age.
    """
    import requests

    try:
        resp = requests.post(
            MESH_QUERY_URL,
            json={
                "type": "directive_queue_metadata",
                "fields": ["handler", "pending", "proposed", "oldest_age_seconds"],
            },
            timeout=5,
        )
        resp.raise_for_status()
        raw_handlers: list[dict] = resp.json().get("handlers", [])
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=INTERNAL_ERROR_MSG) from exc

    handlers = [HandlerQueueStatus(**h) for h in raw_handlers]

    total_pending = sum(h.pending for h in handlers)
    total_proposed = sum(h.proposed for h in handlers)
    oldest_overall = (
        max((h.oldest_age_seconds for h in handlers), default=0)
        if handlers
        else 0
    )

    return QueueHealthResponse(
        handlers=handlers,
        summary=QueueHealthSummary(
            total_pending=total_pending,
            total_proposed=total_proposed,
            oldest_overall_seconds=oldest_overall,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────


def _inmemory_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory SQLite DB with the minimal schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(
        """
        CREATE TABLE orgs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN NOT NULL DEFAULT 1
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            org_id INTEGER NOT NULL REFERENCES orgs(id),
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN NOT NULL DEFAULT 1
        );
        """
    )
    yield conn
    conn.close()


def _seed_handler_counts() -> list[dict]:
    """Return seeded handler queue data matching acceptance criteria."""
    return [
        {"handler": "generate_file", "pending": 3, "proposed": 2, "oldest_age_seconds": 300},
        {"handler": "run_script", "pending": 1, "proposed": 0, "oldest_age_seconds": 60},
    ]


@pytest.fixture
def seeded_client(monkeypatch) -> TestClient:
    """
    Override the mesh_memory HTTP call so the self-test runs offline.
    The seeded data matches the acceptance criteria exactly.
    """
    import requests

    def _mock_post(url: str, **kwargs):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"handlers": _seed_handler_counts()}

        return _Resp()

    monkeypatch.setattr(requests, "post", _mock_post)

    with TestClient(app) as client:
        yield client


def test_queue_health(seeded_client: TestClient):
    """Verify the queue-health endpoint returns the seeded data."""
    response = seeded_client.get(f"{SERVICE_PREFIX}/directives/queue-health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "handlers" in data, "Response missing 'handlers'"
    assert "summary" in data, "Response missing 'summary'"

    handlers = data["handlers"]
    assert len(handlers) == 2, f"Expected 2 handlers, got {len(handlers)}"

    gen_file = next((h for h in handlers if h["handler"] == "generate_file"), None)
    assert gen_file is not None, "handler 'generate_file' not found"
    assert gen_file["pending"] == 3, f"Expected pending=3, got {gen_file['pending']}"
    assert gen_file["proposed"] == 2, f"Expected proposed=2, got {gen_file['proposed']}"
    assert gen_file["oldest_age_seconds"] == 300, (
        f"Expected oldest_age_seconds=300, got {gen_file['oldest_age_seconds']}"
    )

    run_script = next((h for h in handlers if h["handler"] == "run_script"), None)
    assert run_script is not None, "handler 'run_script' not found"
    assert run_script["pending"] == 1, f"Expected pending=1, got {run_script['pending']}"
    assert run_script["proposed"] == 0, f"Expected proposed=0, got {run_script['proposed']}"
    assert run_script["oldest_age_seconds"] == 60, (
        f"Expected oldest_age_seconds=60, got {run_script['oldest_age_seconds']}"
    )

    summary = data["summary"]
    assert summary["total_pending"] == 4, f"Expected total_pending=4, got {summary['total_pending']}"
    assert summary["total_proposed"] == 2, f"Expected total_proposed=2, got {summary['total_proposed']}"
    assert summary["oldest_overall_seconds"] == 300, (
        f"Expected oldest_overall_seconds=300, got {summary['oldest_overall_seconds']}"
    )


if __name__ == "__main__":
    import sys

    from app.db import get_session

    # Apply SQLite override for the self-test
    from app.main import app as fastapi_app

    # Merge our test app routes into the main app for a unified test client
    for route in app.routes:
        if route.path not in [r.path for r in fastapi_app.routes]:
            fastapi_app.routes.append(route)

    @contextmanager
    def _override_get_session():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.executescript(
            """
            CREATE TABLE orgs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN NOT NULL DEFAULT 1
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                org_id INTEGER NOT NULL REFERENCES orgs(id),
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN NOT NULL DEFAULT 1
            );
            """
        )
        yield conn
        conn.close()

    fastapi_app.dependency_overrides[get_session] = _override_get_session

    # Patch requests.post to return seeded data
    import requests

    _original_post = requests.post

    def _patched_post(url: str, **kwargs):
        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"handlers": _seed_handler_counts()}

        return _R()

    requests.post = _patched_post

    try:
        client = TestClient(fastapi_app)
        response = client.get(f"{SERVICE_PREFIX}/directives/queue-health")
        assert response.status_code == 200
        data = response.json()

        assert len(data["handlers"]) == 2
        gen = next((h for h in data["handlers"] if h["handler"] == "generate_file"), None)
        assert gen is not None
        assert gen["oldest_age_seconds"] == 300
        print("PASS")
        sys.exit(0)
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    finally:
        requests.post = _original_post
        fastapi_app.dependency_overrides.clear()