# deps: fastapi, pydantic, sqlalchemy, requests
"""Verdict Distribution Timeline Service.

Provides an endpoint to retrieve a timeline of server verdicts.
Public endpoint -- no authentication required.

GET /api/verdict_distribution_timeline/
  Returns a list of servers with their current verdict and last_assessed timestamp.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Ensure repo root is on sys.path before any app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/verdict_distribution_timeline", tags=["verdict_distribution_timeline"])


class ServerVerdict(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    last_assessed: str | None = None  # ISO 8601


@router.get("/", response_model=List[ServerVerdict])
def get_verdict_timeline(db: Session = Depends(get_session)):
    """Return a list of servers with their latest verdicts."""
    try:
        servers = db.query(McpServerRegistry).all()
        results: List[ServerVerdict] = []
        for srv in servers:
            last_assessed_str: str | None = None
            if srv.last_assessed is not None:
                last_assessed_str = srv.last_assessed.isoformat()
            results.append(
                ServerVerdict(
                    server_id=str(srv.server_id),
                    name=getattr(srv, "name", None),
                    verdict=getattr(srv, "verdict", None),
                    last_assessed=last_assessed_str,
                )
            )
        return results
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    # Set up path BEFORE importing app submodules so Python finds them correctly
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))

    # Import ONLY the submodules we need -- bypass app/__init__.py which has a
    # broken `from app.routers import api_router` that fails in isolation.
    # We access app.db and app.models via their submodules directly.
    import importlib
    _app_db = importlib.import_module("app.db")
    _app_models = importlib.import_module("app.models")
    get_session = _app_db.get_session
    Base = _app_db.Base
    McpServerRegistry = _app_models.McpServerRegistry

    # In-memory SQLite with StaticPool so sessions share the same connection
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        return SessionLocal()

    # Standalone test app with dependency override on the test app instance
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session
    test_app.include_router(router)

    # Seed one record via the test session
    with SessionLocal() as db:
        sample = McpServerRegistry(
            server_id="srv-test-1",
            name="Test Server",
            verdict="low",
            last_assessed=datetime(2024, 6, 15, 12, 0, 0),
        )
        db.add(sample)
        db.commit()

    client = TestClient(test_app)
    resp = client.get("/api/verdict_distribution_timeline/")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code} -- {resp.text}")
        _sys.exit(1)
    data = resp.json()
    if not isinstance(data, list) or len(data) == 0:
        print(f"FAIL: expected non-empty list, got {data}")
        _sys.exit(1)
    # Verify the seeded record is present
    found = any(r["server_id"] == "srv-test-1" and r["verdict"] == "low" for r in data)
    if not found:
        print(f"FAIL: seeded record not found in response: {data}")
        _sys.exit(1)
    print("PASS")
    _sys.exit(0)
