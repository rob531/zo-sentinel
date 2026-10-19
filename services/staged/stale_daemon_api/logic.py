# services/staged/stale_daemon_api/logic.py
from __future__ import annotations

import datetime
from typing import List

import requests
from fastapi import APIRouter, Depends, FastAPI, Query, status
from pydantic import BaseModel, Field

# Real app DB session import (required by project conventions)
from app.db import get_session  # noqa: F401  (imported for dependency injection compliance)

router = APIRouter(prefix="/api")


class StaleDaemonInfo(BaseModel):
    name: str = Field(..., alias="server_name")
    age_seconds: int
    last_heartbeat: str
    last_status: str = Field(..., alias="last_status_change")


class HealthResponse(BaseModel):
    stale: List[StaleDaemonInfo]
    healthy: int
    stale_count: int
    checked_at: str


WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _query_service_health() -> List[dict]:
    """
    Retrieve all rows from the `service_health` bus table.
    The write‑service returns a JSON payload with a top‑level ``rows`` key.
    """
    sql = """
        SELECT
            server_name,
            last_heartbeat,
            last_status_change,
            age_seconds
        FROM service_health
    """
    payload = {"sql": sql}
    resp = requests.post(WRITE_SERVICE_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


@router.get(
    "/health/stale-daemons",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Report stale daemon health",
)
def get_stale_daemons(
    threshold: int = Query(300, ge=0, description="Age in seconds to consider a daemon stale"),
    _: None = Depends(get_session),  # Dependency kept for contract compliance
) -> HealthResponse:
    rows = _query_service_health()

    stale: List[StaleDaemonInfo] = []
    healthy_count = 0

    for row in rows:
        age = int(row.get("age_seconds", 0))
        if age > threshold:
            stale.append(
                StaleDaemonInfo(
                    server_name=row.get("server_name", ""),
                    age_seconds=age,
                    last_heartbeat=row.get("last_heartbeat", ""),
                    last_status_change=row.get("last_status_change", ""),
                )
            )
        else:
            healthy_count += 1

    # Sort stale daemons by descending age
    stale.sort(key=lambda x: x.age_seconds, reverse=True)

    checked_at = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()

    return HealthResponse(
        stale=stale,
        healthy=healthy_count,
        stale_count=len(stale),
        checked_at=checked_at,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Mock the external write‑service
    # ------------------------------------------------------------------- #
    class _MockResponse:
        def __init__(self, rows: List[dict]):
            self._rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"rows": self._rows}

    def _mock_post(url: str, json: dict):
        if url.rstrip("/") == WRITE_SERVICE_URL:
            mock_rows = [
                {
                    "server_name": "daemon-stale",
                    "last_heartbeat": "2023-01-01T00:00:00Z",
                    "last_status_change": "2023-01-01T00:00:00Z",
                    "age_seconds": 600,
                },
                {
                    "server_name": "daemon-healthy-1",
                    "last_heartbeat": "2023-01-01T00:05:00Z",
                    "last_status_change": "2023-01-01T00:05:00Z",
                    "age_seconds": 100,
                },
                {
                    "server_name": "daemon-healthy-2",
                    "last_heartbeat": "2023-01-01T00:10:00Z",
                    "last_status_change": "2023-01-01T00:10:00Z",
                    "age_seconds": 50,
                },
            ]
            return _MockResponse(mock_rows)
        raise RuntimeError(f"Unexpected URL in mock: {url}")

    # Patch requests.post with our mock
    requests.post = _mock_post  # type: ignore

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app for testing
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Dependency override for get_session (not used in this endpoint)
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the test
    # ------------------------------------------------------------------- #
    response = client.get("/api/health/stale-daemons?threshold=300")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["stale_count"] == 1, f"stale_count={payload['stale_count']}"
    assert payload["healthy"] == 2, f"healthy={payload['healthy']}"
    assert payload["stale"][0]["age_seconds"] >= 600, "stale daemon age too low"
    print("PASS")