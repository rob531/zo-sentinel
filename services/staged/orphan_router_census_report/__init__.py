"""Orphan Router Census Report - Service module."""

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class CensusResult(BaseModel):
    orphan_router_count: int
    scored_routers: int
    healthy_routers: int
    degraded_routers: int
    total_capacity_estimate: float


def get_score_status(score: float) -> str:
    """Return health status string for a given score."""
    if score >= 0.8:
        return "healthy"
    elif score >= 0.5:
        return "degraded"
    else:
        return "unhealthy"


def score_to_status(score: float | None) -> str:
    """Convert a score value to status string, handling None."""
    if score is None:
        return "unknown"
    return get_score_status(score)


def compute_orphan_census(session: Session) -> CensusResult:
    """Compute census of orphan routers (registered but un-scored)."""
    result = session.execute(
        text("""
            SELECT 
                COUNT(*) FILTER (WHERE axis_score IS NULL) AS orphan_count,
                COUNT(*) FILTER (WHERE axis_score IS NOT NULL) AS scored_count,
                COUNT(*) FILTER (WHERE axis_score >= 0.8) AS healthy_count,
                COUNT(*) FILTER (WHERE axis_score >= 0.5 AND axis_score < 0.8) AS degraded_count,
                COALESCE(SUM(axis_score), 0) AS total_capacity
            FROM McpServerRegistry r
            LEFT JOIN McpLlmAxisScore s ON s.router_id = r.id
            WHERE r.is_active = true
        """)
    ).fetchone()

    return CensusResult(
        orphan_router_count=result[0] or 0,
        scored_routers=result[1] or 0,
        healthy_routers=result[2] or 0,
        degraded_routers=result[3] or 0,
        total_capacity_estimate=float(result[4] or 0.0),
    )


def generate_census_report(session: Session) -> dict[str, Any]:
    """Generate the full orphan router census report."""
    census = compute_orphan_census(session)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "census": census.model_dump(),
    }


async def get_census_report(session: Session = Depends(get_session)) -> dict[str, Any]:
    """FastAPI dependency for getting census report."""
    return generate_census_report(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                id INTEGER PRIMARY KEY,
                name TEXT,
                is_active BOOLEAN DEFAULT true
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY,
                router_id INTEGER,
                axis_score REAL
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry (id, name, is_active) VALUES
            (1, 'router_a', true),
            (2, 'router_b', true),
            (3, 'router_c', true),
            (4, 'router_d', true)
        """))
        conn.execute(text("""
            INSERT INTO McpLlmAxisScore (id, router_id, axis_score) VALUES
            (1, 1, 0.9),
            (2, 2, 0.5),
            (3, 3, 0.3)
        """))

    test_session = SessionLocal()
    try:
        # Verify self-test assertions
        assert get_score_status(0.9) == "healthy", f"Expected healthy, got {get_score_status(0.9)}"
        assert get_score_status(0.5) == "degraded", f"Expected degraded, got {get_score_status(0.5)}"
        assert get_score_status(0.3) == "unhealthy", f"Expected unhealthy, got {get_score_status(0.3)}"
        assert get_score_status(0.8) == "healthy"
        assert get_score_status(0.49) == "unhealthy"

        census = compute_orphan_census(test_session)
        assert census.orphan_router_count == 1, f"Expected 1 orphan, got {census.orphan_router_count}"
        assert census.scored_routers == 3, f"Expected 3 scored, got {census.scored_routers}"
        assert census.healthy_routers == 1, f"Expected 1 healthy, got {census.healthy_routers}"
        assert census.degraded_routers == 1, f"Expected 1 degraded, got {census.degraded_routers}"

        print("PASS")
    finally:
        test_session.close()