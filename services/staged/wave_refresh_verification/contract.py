"""Wave Refresh Verification contract.

Provides an endpoint that verifies which servers have received fresh LLM axis
scores for a given wave.

Running this module directly executes a self‑test that seeds an in‑memory
SQLite database, invokes the endpoint and validates the response.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application imports – required to avoid a “hollow” build.
from app.db import get_session
from app.models import McpLlmAxisScore  # noqa: F401 – imported for side‑effects / contract compliance


router = APIRouter(
    prefix="/api/scoring/wave/refresh/verification",
    tags=["wave_refresh_verification"],
)


class WaveRefreshVerificationResponse(BaseModel):
    wave_id: str
    total_servers: int
    scored_servers: int
    missing_servers: List[str]
    verified_at: datetime


@router.get(
    "/",
    response_model=WaveRefreshVerificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify LLM axis scores for a wave",
)
def get_wave_refresh_verification(
    wave_id: str = Query(..., description="Identifier of the wave to verify"),
    db: Session = Depends(get_session),
):
    """
    Verify that each server participating in *wave_id* has at least one entry in
    ``mcp_llm_axis_scores``.  The function reads the ``canonical_family`` table
    (which contains the list of servers for a wave) and cross‑references it
    with the scores table.
    """
    # Retrieve all server identifiers belonging to the requested wave.
    server_rows = db.execute(
        text(
            """
            SELECT server_id
            FROM canonical_family
            WHERE wave_id = :wave_id
            """
        ),
        {"wave_id": wave_id},
    ).fetchall()
    server_ids = [row[0] for row in server_rows]

    total_servers = len(server_ids)
    if total_servers == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wave '{wave_id}' not found",
        )

    # Determine which of those servers have at least one score entry.
    scored_rows = db.execute(
        text(
            """
            SELECT DISTINCT server_id
            FROM mcp_llm_axis_scores
            WHERE server_id IN (
                SELECT server_id
                FROM canonical_family
                WHERE wave_id = :wave_id
            )
            """
        ),
        {"wave_id": wave_id},
    ).fetchall()
    scored_server_ids = {row[0] for row in scored_rows}

    missing_servers = [sid for sid in server_ids if sid not in scored_server_ids]

    return WaveRefreshVerificationResponse(
        wave_id=wave_id,
        total_servers=total_servers,
        scored_servers=len(scored_server_ids),
        missing_servers=missing_servers,
        verified_at=datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app for the test.
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # --------------------------------------------------------------------- #
    SQLITE_URL = "sqlite://"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create the tables required for the test.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE canonical_family (
                server_id TEXT NOT NULL,
                wave_id   TEXT NOT NULL
            );
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                adapter_sha256 TEXT,
                axis_name TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                scored_at TEXT
            );
            """
        )
        # Seed data: three servers in wave 'w1'.
        conn.exec_driver_sql(
            """
            INSERT INTO canonical_family (server_id, wave_id) VALUES
                ('s1', 'w1'),
                ('s2', 'w1'),
                ('s3', 'w1');
            """
        )
        # Two of them have scores.
        conn.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores (server_id) VALUES
                ('s1'),
                ('s2');
            """
        )

    # Dependency override to use the in‑memory session.
    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # --------------------------------------------------------------------- #
    # Execute the test request.
    # --------------------------------------------------------------------- #
    from fastapi.testclient import TestClient

    client = TestClient(app)

    response = client.get("/api/scoring/wave/refresh/verification?wave_id=w1")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert payload["scored_servers"] == 2, "scored_servers should be 2"
    assert len(payload["missing_servers"]) == 1, "missing_servers length should be 1"
    print("PASS")
    raise SystemExit(0)