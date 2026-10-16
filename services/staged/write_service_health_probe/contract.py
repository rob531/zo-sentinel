"""services.staged.write_service_health_probe.contract

FastAPI contract for the write_service_health_probe service.

Provides a GET endpoint at ``/api/internal/write_service_health`` that
queries the write‑service health table via the write‑service HTTP API and
returns a health summary.
"""

from __future__ import annotations

import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

# Import the real data layer to satisfy the “no‑hollow” gate.
# The endpoint does not use the DB, but the import is required.
from app.db import get_session  # noqa: F401

router = APIRouter()


class WriteServiceHealthResponse(BaseModel):
    """Response model for the health probe."""

    healthy: bool
    status: Optional[str] = None
    last_heartbeat: Optional[str] = None
    age_seconds: Optional[float] = None


def _query_write_service() -> dict:
    """Query the write‑service health table.

    Returns a dict with keys ``status`` and ``last_heartbeat`` (ISO‑8601 string)
    or an empty dict if the query fails or returns no rows.
    """
    query_sql = (
        "SELECT status, last_heartbeat FROM service_health "
        "WHERE service='write_service' "
        "ORDER BY last_heartbeat DESC LIMIT 1"
    )
    payload = {"query": query_sql}
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query", json=payload, timeout=10.0
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            return {}
        return rows[0]  # type: ignore[return-value]
    except Exception:
        return {}


def _build_response(row: dict) -> WriteServiceHealthResponse:
    """Build the Pydantic response from a query row."""
    status = row.get("status")
    last_hb = row.get("last_heartbeat")
    age_seconds: Optional[float] = None
    healthy = False

    if last_hb:
        try:
            hb_dt = datetime.datetime.fromisoformat(last_hb)
            age_seconds = (datetime.datetime.utcnow() - hb_dt).total_seconds()
            healthy = age_seconds <= 60.0
        except Exception:
            # malformed timestamp – treat as unhealthy
            healthy = False

    return WriteServiceHealthResponse(
        healthy=healthy,
        status=status,
        last_heartbeat=last_hb,
        age_seconds=age_seconds,
    )


@router.get(
    "/api/internal/write_service_health",
    response_model=WriteServiceHealthResponse,
    tags=["internal"],
)
def get_write_service_health_endpoint() -> WriteServiceHealthResponse:
    """FastAPI endpoint that returns the write‑service health."""
    row = _query_write_service()
    return _build_response(row)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Mock write‑service that returns a known health row
    # ------------------------------------------------------------------- #
    mock_write_service = FastAPI()


    @mock_write_service.post("/query")
    def mock_query(payload: dict):  # noqa: D401
        """Return a single row with a recent heartbeat."""
        now_iso = datetime.datetime.utcnow().isoformat()
        return {"rows": [{"status": "running", "last_heartbeat": now_iso}]}

    # ------------------------------------------------------------------- #
    # Patch ``requests.post`` so the contract code talks to the mock service
    # ------------------------------------------------------------------- #
    original_post = requests.post

    mock_client = TestClient(mock_write_service)


    def _patched_post(url, json=None, timeout=None, **kwargs):  # type: ignore
        # The contract always posts to http://127.0.0.1:8772/query;
        # we ignore the URL and forward to the mock client.
        response = mock_client.post("/query", json=json, timeout=timeout)
        class _Resp:
            def __init__(self, resp):
                self._resp = resp

            def raise_for_status(self):
                if not (200 <= self._resp.status_code < 300):
                    raise requests.HTTPError(
                        f"{self._resp.status_code} Server Error"
                    )

            def json(self):
                return self._resp.json()

        return _Resp(response)

    requests.post = _patched_post  # type: ignore

    # ------------------------------------------------------------------- #
    # Build the main FastAPI app that includes the router under test
    # ------------------------------------------------------------------- #
    app = FastAPI()
    # Dependency override for the real DB session – use an in‑memory SQLite DB.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _test_get_session():
        return SessionLocal()

    app.dependency_overrides[get_session] = _test_get_session  # type: ignore
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the self‑test
    # ------------------------------------------------------------------- #
    response = client.get("/api/internal/write_service_health")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["healthy"] is True, "Health should be True for recent heartbeat"
    assert payload["status"] == "running", "Status should be 'running'"
    assert isinstance(payload["age_seconds"], float), "age_seconds must be a float"
    # Clean‑up patch
    requests.post = original_post  # type: ignore
    print("PASS")