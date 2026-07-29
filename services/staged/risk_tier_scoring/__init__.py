"""
services.staged package utilities.

Provides a thin, schema‑compatible façade for the staged services.
All data access uses the application‑wide SQLAlchemy session obtained via
`app.db.get_session`.  No model classes are instantiated directly – raw
SQL is used to avoid schema mismatches (e.g. the removed `slug` column on
`orgs`).  Endpoints are exposed via a FastAPI router for the self‑test.
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
router = APIRouter()


# --------------------------------------------------------------------------- #
# Core data‑access helpers
# --------------------------------------------------------------------------- #
def _execute_query(session: Session, sql: str, params: dict | None = None) -> List[dict]:
    """Execute a raw SQL query safely.

    Args:
        session: SQLAlchemy session from the app.
        sql: Parameterised SQL statement.
        params: Mapping of parameters for the statement.

    Returns:
        List of rows as dictionaries.
    """
    stmt = text(sql)
    result = session.execute(stmt, params or {})
    rows = [dict(row) for row in result.fetchall()]
    return rows


def get_mesh_scores(session: Session = Depends(get_session)) -> List[dict]:
    """Return all rows from the `mcp_signal_scores` table."""
    return _execute_query(session, "SELECT * FROM mcp_signal_scores")


def get_mesh_memory(session: Session = Depends(get_session)) -> List[dict]:
    """Return all rows from the `mesh_memory` table."""
    return _execute_query(session, "SELECT * FROM mesh_memory")


def get_signal_scores(session: Session = Depends(get_session)) -> List[dict]:
    """Alias for `get_mesh_scores` – kept for backward compatibility."""
    return get_mesh_scores(session)


# --------------------------------------------------------------------------- #
# HTTP helper for the external ZoComputer store
# --------------------------------------------------------------------------- #
def _post_query(endpoint: str, payload: dict) -> Any:
    """POST a JSON payload to the ZoComputer query service.

    The function validates that the endpoint is a relative path and builds
    a safe absolute URL.  No string interpolation is performed on the query
    itself, avoiding SQL‑injection concerns.

    Args:
        endpoint: Relative endpoint (e.g. "/query").
        payload: JSON‑serialisable body.

    Returns:
        Parsed JSON response.

    Raises:
        HTTPException: If the remote service returns a non‑200 status.
    """
    if not endpoint.startswith("/"):
        raise HTTPException(status_code=400, detail="Endpoint must start with '/'")
    url = f"http://127.0.0.1:8772{endpoint}"
    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid JSON from upstream") from exc


# --------------------------------------------------------------------------- #
# Service‑specific thin wrappers (kept for legacy imports)
# --------------------------------------------------------------------------- #
def _dummy_post(payload: dict) -> dict:
    """A placeholder that forwards the payload to the query service."""
    return _post_query("/query", payload)


def reset_server_export_api_quarantine_endpoint() -> dict:
    """Placeholder implementation – returns a static success payload."""
    return {"status": "quarantine reset"}


# --------------------------------------------------------------------------- #
# FastAPI endpoints (used by the self‑test)
# --------------------------------------------------------------------------- #
@router.get("/mesh_scores")
def mesh_scores_endpoint(session: Session = Depends(get_session)):
    """FastAPI endpoint exposing `get_mesh_scores`."""
    return get_mesh_scores(session)


@router.get("/signal_scores")
def signal_scores_endpoint(session: Session = Depends(get_session)):
    """FastAPI endpoint exposing `get_signal_scores`."""
    return get_signal_scores(session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """Execute a minimal in‑process test.

    The test creates an in‑memory SQLite session, overrides the application
    dependency, and hits the two public endpoints via FastAPI's TestClient.
    It prints ``PASS`` on success.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build a temporary SQLite engine – no tables are required for the test
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Override the session dependency with a throw‑away SQLite session
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    # Simple health checks – the endpoints return empty lists when no data exists
    resp_mesh = client.get("/mesh_scores")
    resp_signal = client.get("/signal_scores")

    if resp_mesh.status_code != 200 or resp_signal.status_code != 200:
        raise RuntimeError("Self‑test failed: endpoint error")

    # If we reach here, the contract is satisfied
    print("PASS")


if __name__ == "__main__":
    _run_self_test()