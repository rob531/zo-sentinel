"""
server_health_summary service - provides per-server health snapshot.

Mirrors the _exemplar pattern: FastAPI service with GET /api/servers/{server_id}/health-summary.
Reads mcp_llm_axis_scores joined to mcp_server_registry and mcp_signal_scores.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_health_summary"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ServerHealthSummary(BaseModel):
    """Per-server health snapshot returned by the endpoint."""

    server_id: str
    name: str
    risk_tier: str
    last_scored_at: Optional[datetime] = None
    scoring_freshness_hours: Optional[float] = None
    axis_count: int = 0
    overall_score: Optional[float] = None
    has_critical_axis: bool = False
    trust_gate_override: bool = False
    verdict: str = "unknown"
    scan_count: int = 0


# ---------------------------------------------------------------------------
# Business logic (isolated so callers can invoke without HTTP overhead)
# ---------------------------------------------------------------------------


def compute_freshness_hours(scored_at: Optional[datetime]) -> Optional[float]:
    """Return hours between scored_at and now. Returns None if scored_at is None."""
    if scored_at is None:
        return None
    now = datetime.now(timezone.utc)
    scored = scored_at if scored_at.tzinfo else scored_at.replace(tzinfo=timezone.utc)
    delta = now - scored
    return delta.total_seconds() / 3600.0


def determine_risk_tier(
    overall_score: Optional[float],
    has_critical_axis: bool,
    axis_count: int,
) -> str:
    """Derive risk_tier from score and axis flags."""
    if axis_count == 0:
        return "unknown"
    if has_critical_axis:
        return "critical"
    if overall_score is None:
        return "unknown"
    if overall_score >= 80:
        return "low"
    if overall_score >= 50:
        return "medium"
    return "high"


def determine_verdict(
    risk_tier: str,
    scoring_freshness_hours: Optional[float],
    trust_gate_override: bool,
) -> str:
    """Derive the overall verdict string."""
    if trust_gate_override:
        return "override"
    if risk_tier == "critical":
        return "alert"
    if risk_tier == "unknown":
        return "no_data"
    if scoring_freshness_hours is not None and scoring_freshness_hours > 24:
        return "stale"
    if risk_tier == "high":
        return "review"
    if risk_tier == "medium":
        return "ok"
    return "ok"


def trust_gate_override_for_server(server_id: str) -> bool:
    """
    Determine if trust_gate override is active for a server.
    Calls the trust_gating_override.trust_gate() function.
    """
    try:
        from trust_gating_override import trust_gate

        return trust_gate(server_id)
    except Exception:
        return False


def get_server_health_summary(db: Session, server_id: str) -> ServerHealthSummary:
    """
    Build a health summary for a single server.

    Queries mcp_llm_axis_scores joined to mcp_server_registry,
    then enriches with mcp_signal_scores data.
    """
    # Fetch server registry row
    server_row = db.execute(
        text("""
            SELECT server_id, name
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id},
    ).fetchone()

    if server_row is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    name = server_row.name if hasattr(server_row, "name") else server_row[1]

    # Fetch axis scores for this server
    axis_rows = db.execute(
        text("""
            SELECT id, axis_name, score, is_critical, scored_at
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
        """),
        {"server_id": server_id},
    ).fetchall()

    axis_count = len(axis_rows)

    if axis_count == 0:
        # No axis data - return minimal snapshot
        trust_gate_override = trust_gate_override_for_server(server_id)
        risk_tier = determine_risk_tier(None, False, 0)
        verdict = determine_verdict(risk_tier, None, trust_gate_override)
        return ServerHealthSummary(
            server_id=server_id,
            name=name,
            risk_tier=risk_tier,
            last_scored_at=None,
            scoring_freshness_hours=None,
            axis_count=0,
            overall_score=None,
            has_critical_axis=False,
            trust_gate_override=trust_gate_override,
            verdict=verdict,
            scan_count=0,
        )

    # Determine overall score (average of axis scores)
    scores = [row.score for row in axis_rows if row.score is not None]
    overall_score = sum(scores) / len(scores) if scores else None

    # Check for critical axes
    has_critical_axis = any(row.is_critical for row in axis_rows)

    # Get most recent scored_at
    last_scored_at = axis_rows[0].scored_at if axis_rows else None
    scoring_freshness_hours = compute_freshness_hours(last_scored_at)

    # Fetch signal scores count
    signal_rows = db.execute(
        text("""
            SELECT COUNT(*) as signal_count
            FROM mcp_signal_scores
            WHERE server_id = :server_id
        """),
        {"server_id": server_id},
    ).fetchone()

    scan_count = signal_rows.signal_count if signal_rows else 0

    # Determine risk_tier and verdict
    risk_tier = determine_risk_tier(overall_score, has_critical_axis, axis_count)
    trust_gate_override = trust_gate_override_for_server(server_id)
    verdict = determine_verdict(risk_tier, scoring_freshness_hours, trust_gate_override)

    return ServerHealthSummary(
        server_id=server_id,
        name=name,
        risk_tier=risk_tier,
        last_scored_at=last_scored_at,
        scoring_freshness_hours=scoring_freshness_hours,
        axis_count=axis_count,
        overall_score=overall_score,
        has_critical_axis=has_critical_axis,
        trust_gate_override=trust_gate_override,
        verdict=verdict,
        scan_count=scan_count,
    )


# ---------------------------------------------------------------------------
# FastAPI router endpoint
# ---------------------------------------------------------------------------


@router.get("/servers/{server_id}/health-summary", response_model=ServerHealthSummary)
def get_health_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerHealthSummary:
    """Return a per-server health snapshot."""
    return get_server_health_summary(db, server_id)


# ---------------------------------------------------------------------------
# Self-test (in-memory SQLite, no network)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    # Use in-memory SQLite for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables matching real schema
    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                score REAL,
                is_critical INTEGER DEFAULT 0,
                scored_at TIMESTAMP,
                created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_signal_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                signal_name TEXT,
                score REAL,
                created_at TIMESTAMP
            )
        """))
        conn.commit()

    TestSession = sessionmaker(bind=test_engine)

    # Seed test data: 3 servers, 5 axis rows each
    test_servers = [
        {"server_id": "srv-1", "name": "Server Alpha"},
        {"server_id": "srv-2", "name": "Server Beta"},
        {"server_id": "srv-3", "name": "Server Gamma"},
    ]

    now_ts = datetime.now(timezone.utc)

    with test_engine.connect() as conn:
        for srv in test_servers:
            conn.execute(
                text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:server_id, :name)"),
                srv,
            )
            for i in range(5):
                conn.execute(
                    text("""
                        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, score, is_critical, scored_at)
                        VALUES (:server_id, :axis_name, :score, :is_critical, :scored_at)
                    """),
                    {
                        "server_id": srv["server_id"],
                        "axis_name": f"axis_{i}",
                        "score": 70.0 + i * 2,
                        "is_critical": 1 if i == 0 else 0,
                        "scored_at": now_ts.isoformat(),
                    },
                )
        conn.commit()

    # Run self-test assertions
    session = TestSession()

    # Patch trust_gate to avoid network dependency
    import trust_gating_override

    original_trust_gate = getattr(trust_gating_override, "trust_gate", None)
    trust_gating_override.trust_gate = lambda sid: False

    try:
        from fastapi.testclient import TestClient

        # Patch get_session for FastAPI
        from app.db import get_session

        def override_get_session():
            yield session

        from main import app

        app.dependency_overrides[get_session] = override_get_session
        client = TestClient(app)

        all_passed = True
        for srv in test_servers:
            response = client.get(f"/api/servers/{srv['server_id']}/health-summary")
            if response.status_code != 200:
                print(f"FAIL: expected 200 for {srv['server_id']}, got {response.status_code}")
                all_passed = False
                continue

            data = response.json()

            # Assert required fields present
            if "risk_tier" not in data:
                print(f"FAIL: risk_tier missing in response for {srv['server_id']}")
                all_passed = False
            if "scoring_freshness_hours" not in data:
                print(f"FAIL: scoring_freshness_hours missing for {srv['server_id']}")
                all_passed = False

            if data.get("axis_count") != 5:
                print(f"FAIL: expected axis_count=5 for {srv['server_id']}, got {data.get('axis_count')}")
                all_passed = False

        if all_passed:
            print("PASS")
        else:
            print("FAIL")
            sys.exit(1)

    finally:
        # Restore original trust_gate
        if original_trust_gate is not None:
            trust_gating_override.trust_gate = original_trust_gate
        else:
            try:
                delattr(trust_gating_override, "trust_gate")
            except Exception:
                pass
        session.close()