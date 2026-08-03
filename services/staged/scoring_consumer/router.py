"""
Scoring-consumer router: reads axis scores, computes risk tiers, persists results.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

# ---------------------------------------------------------------------------
# Risk-tier thresholds
# ---------------------------------------------------------------------------
_TRUSTED_GENERAL = 75
_TRUSTED_RESEARCH = 60
_ENTERPRISE_CONTROLLED = 45
_CAUTION_LIMITED = 30
_HIGH_RISK_ISOLATED = 15


def risk_tier_from_scores(axes: list[dict]) -> str:
    """
    Compute the overall risk tier from a list of axis score dicts.
    Each dict must contain a 'score' key (0-100).
    Returns one of: TRUSTED_GENERAL, TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED,
                    CAUTION_LIMITED, HIGH_RISK_ISOLATED, KNOWN_THREAT
    """
    if not axes:
        return "KNOWN_THREAT"

    # Use the overall_risk axis if present; otherwise average all axis scores
    overall_axes = [a for a in axes if a.get("axis_name") == "overall_risk"]
    if overall_axes:
        score = float(overall_axes[0].get("score", 0))
    else:
        scores = [a.get("score", 0) for a in axes if isinstance(a.get("score"), (int, float))]
        score = sum(scores) / len(scores) if scores else 0

    if score >= _TRUSTED_GENERAL:
        return "TRUSTED_GENERAL"
    elif score >= _TRUSTED_RESEARCH:
        return "TRUSTED_RESEARCH"
    elif score >= _ENTERPRISE_CONTROLLED:
        return "ENTERPRISE_CONTROLLED"
    elif score >= _CAUTION_LIMITED:
        return "CAUTION_LIMITED"
    elif score >= _HIGH_RISK_ISOLATED:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ConsumeResponse(BaseModel):
    server_id: uuid.UUID
    risk_tier: str
    axes_summary: dict[str, Any]
    computed_at: datetime


class RefreshAllResponse(BaseModel):
    refreshed_count: int
    refreshed_servers: list[uuid.UUID]
    computed_at: datetime


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/consume", response_model=ConsumeResponse)
def consume(
    server_id: uuid.UUID = Query(..., description="MCP server UUID"),
    session: Session = Depends(get_session),
) -> ConsumeResponse:
    """
    Read axis scores for the given server_id and compute the risk tier.
    """
    # Fetch axis scores
    result = session.execute(
        text("""
            SELECT axis_name, score
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
            ORDER BY created_at DESC
        """),
        {"server_id": str(server_id)},
    )
    rows = result.fetchall()

    axes: list[dict] = [{"axis_name": r.axis_name, "score": r.score} for r in rows]
    risk_tier = risk_tier_from_scores(axes)

    # Compute axes summary
    if axes:
        scores = [a["score"] for a in axes if isinstance(a["score"], (int, float))]
        axes_summary = {
            "axis_count": len(axes),
            "average_score": round(sum(scores) / len(scores), 2) if scores else None,
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
        }
    else:
        axes_summary = {"axis_count": 0}

    # Update risk_tier on the server registry
    session.execute(
        text("""
            UPDATE McpServerRegistry
            SET risk_tier = :risk_tier, updated_at = :updated_at
            WHERE id = :server_id
        """),
        {"risk_tier": risk_tier, "updated_at": datetime.now(timezone.utc), "server_id": str(server_id)},
    )
    session.commit()

    return ConsumeResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        axes_summary=axes_summary,
        computed_at=datetime.now(timezone.utc),
    )


@router.post("/refresh-all", response_model=RefreshAllResponse)
def refresh_all(
    session: Session = Depends(get_session),
) -> RefreshAllResponse:
    """
    Iterate all servers that have axis scores in the last 30 days
    and recompute their risk tiers.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # Find servers with recent axis scores
    result = session.execute(
        text("""
            SELECT DISTINCT server_id
            FROM McpLlmAxisScore
            WHERE created_at >= :cutoff
        """),
        {"cutoff": cutoff},
    )
    server_rows = result.fetchall()
    refreshed_servers: list[uuid.UUID] = []

    for row in server_rows:
        server_id = uuid.UUID(row.server_id)

        # Re-fetch axis scores for this server
        scores_result = session.execute(
            text("""
                SELECT axis_name, score
                FROM McpLlmAxisScore
                WHERE server_id = :server_id
                ORDER BY created_at DESC
            """),
            {"server_id": str(server_id)},
        )
        axes: list[dict] = [
            {"axis_name": r.axis_name, "score": r.score}
            for r in scores_result.fetchall()
        ]

        risk_tier = risk_tier_from_scores(axes)

        session.execute(
            text("""
                UPDATE McpServerRegistry
                SET risk_tier = :risk_tier, updated_at = :updated_at
                WHERE id = :server_id
            """),
            {
                "risk_tier": risk_tier,
                "updated_at": datetime.now(timezone.utc),
                "server_id": str(server_id),
            },
        )
        refreshed_servers.append(server_id)

    session.commit()

    return RefreshAllResponse(
        refreshed_count=len(refreshed_servers),
        refreshed_servers=refreshed_servers,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Health / heartbeat
# ---------------------------------------------------------------------------
_last_heartbeat: datetime | None = None


def health() -> dict[str, Any]:
    """Return service health status and heartbeat."""
    global _last_heartbeat
    now = datetime.now(timezone.utc)
    _last_heartbeat = now
    return {
        "status": "ok",
        "service": "scoring_consumer",
        "heartbeat": now.isoformat(),
    }


def send_heartbeat() -> dict[str, Any]:
    """Emit a heartbeat (used by dependent services)."""
    return health()


# ---------------------------------------------------------------------------
# Standalone self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sqlite3
    import sys
    from pathlib import Path

    # Use an in-memory SQLite for self-test only
    DB_PATH = ":memory:"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Create tables (SQLite-compatible schema for testing)
    cur.execute("""
        CREATE TABLE McpServerRegistry (
            id TEXT PRIMARY KEY,
            name TEXT,
            risk_tier TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            axis_name TEXT,
            score REAL,
            created_at TEXT
        )
    """)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Seed server 1: TRUSTED_GENERAL (average >= 75)
    server1_id = "00000000-0000-0000-0000-000000000001"
    server1_name = "test-trusted-server"
    cur.execute(
        "INSERT INTO McpServerRegistry (id, name, risk_tier, updated_at) VALUES (?, ?, ?, ?)",
        (server1_id, server1_name, None, now_iso),
    )
    axes_trusted = [
        ("overall_risk", 85),
        ("auth_strength", 80),
        ("capability_breadth", 75),
        ("data_sensitivity", 90),
        ("network_egress", 70),
        ("maintainer_trust", 88),
        ("exploit_surface", 78),
    ]
    for axis_name, score in axes_trusted:
        cur.execute(
            "INSERT INTO McpLlmAxisScore (server_id, axis_name, score, created_at) VALUES (?, ?, ?, ?)",
            (server1_id, axis_name, score, now_iso),
        )

    # Seed server 2: CAUTION_LIMITED (average >= 30 but < 45)
    server2_id = "00000000-0000-0000-0000-000000000002"
    server2_name = "test-caution-server"
    cur.execute(
        "INSERT INTO McpServerRegistry (id, name, risk_tier, updated_at) VALUES (?, ?, ?, ?)",
        (server2_id, server2_name, None, now_iso),
    )
    axes_caution = [
        ("overall_risk", 35),
        ("auth_strength", 40),
        ("capability_breadth", 30),
        ("data_sensitivity", 38),
        ("network_egress", 28),
        ("maintainer_trust", 32),
        ("exploit_surface", 34),
    ]
    for axis_name, score in axes_caution:
        cur.execute(
            "INSERT INTO McpLlmAxisScore (server_id, axis_name, score, created_at) VALUES (?, ?, ?, ?)",
            (server2_id, axis_name, score, now_iso),
        )

    conn.commit()

    # Simulate consume() for each server
    for server_id, expected_tier in [
        (server1_id, "TRUSTED_GENERAL"),
        (server2_id, "CAUTION_LIMITED"),
    ]:
        cur.execute(
            "SELECT axis_name, score FROM McpLlmAxisScore WHERE server_id = ?",
            (server_id,),
        )
        axes = [{"axis_name": r[0], "score": r[1]} for r in cur.fetchall()]
        tier = risk_tier_from_scores(axes)

        if tier != expected_tier:
            print(f"FAIL: server {server_id} expected {expected_tier}, got {tier}")
            sys.exit(1)

        # Update registry
        cur.execute(
            "UPDATE McpServerRegistry SET risk_tier = ?, updated_at = ? WHERE id = ?",
            (tier, now_iso, server_id),
        )
        conn.commit()

    print("PASS")
    conn.close()