"""
services/staged/server_cve_search_consumer/contract.py

FastAPI contract for the `server_cve_search_consumer` service.

Provides:
    GET /servers/{server_id}/cves
        Returns CVE information associated with a given server.

The module mirrors the structure of `services/_exemplar/contract.py` and
includes a self‑test that can be run with:
    python -m services.staged.server_cve_search_consumer.contract
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real application dependencies – must be imported exactly as they exist.
from app.db import get_session  # noqa: F401  (used via Depends)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #
class CveEntry(BaseModel):
    id: str
    summary: str
    severity: str
    published_at: datetime


class ServerCveResponse(BaseModel):
    server_id: int
    cves: List[CveEntry]


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCveResponse,
    tags=["server_cve_search_consumer"],
)
def get_server_cves(
    server_id: int,
    session: Session = Depends(get_session),
) -> ServerCveResponse:
    """
    Retrieve CVE entries for a specific server.

    The underlying data is stored in a table named ``server_cve`` with the
    columns: ``server_id``, ``cve_id``, ``summary``, ``severity``,
    ``published_at``.
    """
    rows = session.execute(
        text(
            """
            SELECT cve_id, summary, severity, published_at
            FROM server_cve
            WHERE server_id = :sid
            """
        ),
        {"sid": server_id},
    ).fetchall()

    cve_list = [
        CveEntry(
            id=row.cve_id,
            summary=row.summary,
            severity=row.severity,
            published_at=row.published_at,
        )
        for row in rows
    ]

    return ServerCveResponse(server_id=server_id, cves=cve_list)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors the real DB session interface)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create the minimal schema required for the endpoint
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE server_cve (
                server_id   INTEGER NOT NULL,
                cve_id      TEXT    NOT NULL,
                summary     TEXT    NOT NULL,
                severity    TEXT    NOT NULL,
                published_at DATETIME NOT NULL
            );
            """
        )
        # Seed three CVE rows for server_id = 1
        conn.exec_driver_sql(
            """
            INSERT INTO server_cve (server_id, cve_id, summary, severity, published_at)
            VALUES
                (1, 'CVE-1111-1111', 'Test summary 1', 'HIGH',   '2023-01-01T00:00:00'),
                (1, 'CVE-2222-2222', 'Test summary 2', 'MEDIUM', '2023-02-01T00:00:00'),
                (1, 'CVE-3333-3333', 'Test summary 3', 'LOW',    '2023-03-01T00:00:00');
            """
        )

    # Dependency override that supplies sessions bound to the in‑memory engine
    def get_test_session() -> Session:
        SessionLocal = sessionmaker(bind=engine)
        return SessionLocal()

    # Assemble a temporary FastAPI app with the router
    app = FastAPI()
    app.include_router(router)
    # Override the real ``get_session`` dependency with our test version
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the acceptance test
    # ------------------------------------------------------------------- #
    response = client.get("/servers/1/cves")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert payload["server_id"] == 1, "Incorrect server_id in response"
    assert isinstance(payload["cves"], list), "cves field is not a list"
    assert len(payload["cves"]) == 3, f"Expected 3 CVEs, got {len(payload['cves'])}"
    print("PASS")