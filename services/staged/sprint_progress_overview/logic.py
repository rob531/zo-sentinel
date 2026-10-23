"""
Sprint Progress Overview Service

Provides overview metrics for sprint progress including:
- Registry statistics (total, scored, never scored, by tier)
- Scoring velocity (24h, 7d, 30d)
- Build backlog estimation
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/api/overview", tags=["sprint-progress"])


# Response models
class RegistryMetrics(BaseModel):
    total: int = Field(description="Total servers in registry")
    scored: int = Field(description="Servers that have been scored at least once")
    never_scored: int = Field(description="Servers that have never been scored")
    by_tier: Dict[str, int] = Field(
        default_factory=dict, description="Count of servers per risk tier"
    )


class VelocityMetrics(BaseModel):
    daily_24h: int = Field(description="Servers scored in last 24 hours")
    weekly_7d: int = Field(description="Servers scored in last 7 days")
    monthly_30d: int = Field(description="Servers scored in last 30 days")


class BuildBacklogMetrics(BaseModel):
    staged_count: int = Field(description="Count of staged router files")
    mounted_count: int = Field(description="Count of mounted/active services")


class SprintProgressResponse(BaseModel):
    registry: RegistryMetrics
    velocity: VelocityMetrics
    build_backlog: BuildBacklogMetrics


def get_staged_router_count() -> int:
    """Count router files in services/staged directory."""
    try:
        staged_path = "/app/services/staged"
        if os.path.exists(staged_path):
            files = os.listdir(staged_path)
            return sum(1 for f in files if f.endswith(".router"))
    except (OSError, PermissionError):
        pass
    return 0


def get_mounted_service_count() -> int:
    """Estimate mounted services count from static manifest or directory."""
    try:
        mounted_path = "/app/services/mounted"
        if os.path.exists(mounted_path):
            files = os.listdir(mounted_path)
            return sum(1 for f in files if f.endswith(".router"))
    except (OSError, PermissionError):
        pass
    return 0


async def query_mesh_store(
    sql_query: str, params: Optional[Dict[str, Any]] = None
) -> list[Dict[str, Any]]:
    """Query the mesh/pipeline store via write_service."""
    payload = {"sql": sql_query, "params": params or {}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://127.0.0.1:8772/query", json=payload, timeout=30.0
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])


async def get_sprint_progress(
    session: AsyncSession = Depends(get_session),
) -> SprintProgressResponse:
    """
    Compute sprint progress overview metrics.

    Reads from:
    - mcp_server_registry (app Postgres)
    - mcp_llm_axis_scores (app Postgres via MAX(scored_at))
    - cadence_job_runs (mesh store via write_service)
    """
    # Query 1: Get registry counts from mcp_server_registry
    registry_query = text("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN risk_tier IS NOT NULL THEN 1 END) as with_tier
        FROM mcp_server_registry
    """)
    registry_result = await session.execute(registry_query)
    registry_row = registry_result.fetchone()
    total_servers = registry_row[0] if registry_row else 0

    # Query 2: Get scored vs never-scored using MAX(scored_at) from mcp_llm_axis_scores
    # Join with mcp_server_registry to get all servers
    scored_query = text("""
        SELECT
            COUNT(DISTINCT r.id) as scored_count
        FROM mcp_server_registry r
        INNER JOIN mcp_llm_axis_scores s ON r.id = s.server_id
        WHERE s.scored_at IS NOT NULL
    """)
    scored_result = await session.execute(scored_query)
    scored_row = scored_result.fetchone()
    scored_count = scored_row[0] if scored_row else 0
    never_scored_count = total_servers - scored_count

    # Query 3: Per-tier counts from mcp_server_registry
    tier_query = text("""
        SELECT
            COALESCE(risk_tier, 'unknown') as tier,
            COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY risk_tier
    """)
    tier_result = await session.execute(tier_query)
    by_tier = {row[0]: row[1] for row in tier_result.fetchall()}

    # Query 4: Scoring velocity from cadence_job_runs
    # Uses write_service /query for mesh/pipeline tables
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    velocity_query = text("""
        SELECT
            job,
            COUNT(*) FILTER (WHERE completed_at >= :day_ago) as daily_24h,
            COUNT(*) FILTER (WHERE completed_at >= :week_ago) as weekly_7d,
            COUNT(*) FILTER (WHERE completed_at >= :month_ago) as monthly_30d
        FROM cadence_job_runs
        WHERE job = 'score_batch'
        GROUP BY job
    """)

    velocity_metrics = {"daily_24h": 0, "weekly_7d": 0, "monthly_30d": 0}

    try:
        mesh_result = await query_mesh_store(
            """
            SELECT
                COUNT(*) FILTER (WHERE completed_at >= NOW() - INTERVAL '24 hours') as daily_24h,
                COUNT(*) FILTER (WHERE completed_at >= NOW() - INTERVAL '7 days') as weekly_7d,
                COUNT(*) FILTER (WHERE completed_at >= NOW() - INTERVAL '30 days') as monthly_30d
            FROM cadence_job_runs
            WHERE job = 'score_batch'
            """,
            {},
        )
        if mesh_result:
            velocity_metrics = {
                "daily_24h": mesh_result[0].get("daily_24h", 0) or 0,
                "weekly_7d": mesh_result[0].get("weekly_7d", 0) or 0,
                "monthly_30d": mesh_result[0].get("monthly_30d", 0) or 0,
            }
    except (httpx.HTTPError, Exception):
        # Fallback: try direct session query for cadence_job_runs if accessible
        try:
            cadence_query = text("""
                SELECT
                    COUNT(*) FILTER (WHERE completed_at >= :day_ago) as daily_24h,
                    COUNT(*) FILTER (WHERE completed_at >= :week_ago) as weekly_7d,
                    COUNT(*) FILTER (WHERE completed_at >= :month_ago) as monthly_30d
                FROM cadence_job_runs
                WHERE job = 'score_batch'
            """)
            cadence_result = await session.execute(
                cadence_query,
                {"day_ago": day_ago, "week_ago": week_ago, "month_ago": month_ago},
            )
            cadence_row = cadence_result.fetchone()
            if cadence_row:
                velocity_metrics = {
                    "daily_24h": cadence_row[0] or 0,
                    "weekly_7d": cadence_row[1] or 0,
                    "monthly_30d": cadence_row[2] or 0,
                }
        except Exception:
            pass

    # Build backlog metrics
    staged_count = get_staged_router_count()
    mounted_count = get_mounted_service_count()

    return SprintProgressResponse(
        registry=RegistryMetrics(
            total=total_servers,
            scored=scored_count,
            never_scored=never_scored_count,
            by_tier=by_tier,
        ),
        velocity=VelocityMetrics(
            daily_24h=velocity_metrics["daily_24h"],
            weekly_7d=velocity_metrics["weekly_7d"],
            monthly_30d=velocity_metrics["monthly_30d"],
        ),
        build_backlog=BuildBacklogMetrics(
            staged_count=staged_count,
            mounted_count=mounted_count,
        ),
    )


# =============================================================================
# DEPENDENCY FUNCTIONS FOR OTHER SERVICES
# =============================================================================


async def get_registry_stats(session: AsyncSession) -> Dict[str, Any]:
    """Get basic registry statistics for other services."""
    result = await get_sprint_progress(session)
    return {
        "total": result.registry.total,
        "scored": result.registry.scored,
        "never_scored": result.registry.never_scored,
    }


async def get_scoring_velocity(session: AsyncSession) -> Dict[str, int]:
    """Get scoring velocity metrics for other services."""
    result = await get_sprint_progress(session)
    return {
        "daily_24h": result.velocity.daily_24h,
        "weekly_7d": result.velocity.weekly_7d,
        "monthly_30d": result.velocity.monthly_30d,
    }


async def get_tier_counts(session: AsyncSession) -> Dict[str, int]:
    """Get server counts by risk tier for other services."""
    result = await get_sprint_progress(session)
    return result.registry.by_tier


# =============================================================================
# SELF-TEST (runs with: python -m services.staged.sprint_progress_overview.logic)
# =============================================================================

if __name__ == "__main__":
    import asyncio
    import sqlite3
    from unittest.mock import AsyncMock, MagicMock, patch

    # Create in-memory SQLite database for testing
    def setup_test_db():
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()

        # Create tables matching production schema
        cursor.execute("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                score REAL,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE cadence_job_runs (
                id INTEGER PRIMARY KEY,
                job TEXT NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT
            )
        """)

        # Seed 3 servers: 2 scored, 1 never scored
        cursor.execute(
            "INSERT INTO mcp_server_registry (id, name, risk_tier) VALUES (1, 'server_a', 'high')"
        )
        cursor.execute(
            "INSERT INTO mcp_server_registry (id, name, risk_tier) VALUES (2, 'server_b', 'medium')"
        )
        cursor.execute(
            "INSERT INTO mcp_server_registry (id, name, risk_tier) VALUES (3, 'server_c', 'low')"
        )

        # Score 2 servers (server_a and server_b)
        cursor.execute(
            "INSERT INTO mcp_llm_axis_scores (server_id, scored_at) VALUES (1, datetime('now'))"
        )
        cursor.execute(
            "INSERT INTO mcp_llm_axis_scores (server_id, scored_at) VALUES (2, datetime('now'))"
        )
        # server_c is never scored (no entry in mcp_llm_axis_scores)

        # Add some cadence job runs
        cursor.execute(
            "INSERT INTO cadence_job_runs (job, completed_at) VALUES ('score_batch', datetime('now'))"
        )
        cursor.execute(
            "INSERT INTO cadence_job_runs (job, completed_at) VALUES ('score_batch', datetime('now', '-2 days'))"
        )
        cursor.execute(
            "INSERT INTO cadence_job_runs (job, completed_at) VALUES ('score_batch', datetime('now', '-5 days'))"
        )

        conn.commit()
        return conn

    async def run_test():
        test_conn = setup_test_db()

        # Create a mock session that uses our SQLite connection
        class MockAsyncSession:
            def __init__(self, conn):
                self.conn = conn
                self._execution_options = {}

            async def execute(self, query, params=None):
                # Convert SQLAlchemy query to SQLite
                sql = str(query)
                params = params or {}

                # Handle parameterized queries
                if params:
                    # SQLite doesn't support named params in same format, convert
                    for key, value in params.items():
                        if isinstance(value, datetime):
                            params[key] = value.isoformat()

                cursor = self.conn.cursor()
                cursor.execute(sql, params)

                # Return mock result
                class MockResult:
                    def __init__(self, cursor):
                        self._cursor = cursor

                    def fetchone(self):
                        rows = self._cursor.fetchall()
                        if rows:
                            return rows[0]
                        return None

                    def fetchall(self):
                        return self._cursor.fetchall()

                return MockResult(cursor)

            async def close(self):
                pass

        # Mock write_service /query
        async def mock_query_mesh_store(sql, params):
            # Return mock cadence data for the test
            return [
                {
                    "daily_24h": 1,
                    "weekly_7d": 2,
                    "monthly_30d": 3,
                }
            ]

        # Patch dependencies
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.json.return_value = {"rows": [{"daily_24h": 1, "weekly_7d": 2, "monthly_30d": 3}]}
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()

            # Run the actual computation
            mock_session = MockAsyncSession(test_conn)

            # Patch get_staged_router_count and get_mounted_service_count
            with patch(
                "services.staged.sprint_progress_overview.logic.get_staged_router_count",
                return_value=5,
            ):
                with patch(
                    "services.staged.sprint_progress_overview.logic.get_mounted_service_count",
                    return_value=20,
                ):
                    result = await get_sprint_progress(mock_session)

        # Assertions
        assert result.registry.total == 3, f"Expected total=3, got {result.registry.total}"
        assert result.registry.scored == 2, f"Expected scored=2, got {result.registry.scored}"
        assert result.registry.never_scored == 1, f"Expected never_scored=1, got {result.registry.never_scored}"
        assert result.registry.by_tier == {
            "high": 1,
            "medium": 1,
            "low": 1,
        }, f"Expected by_tier={{'high':1,'medium':1,'low':1}}, got {result.registry.by_tier}"

        print(f"Registry: total={result.registry.total}, scored={result.registry.scored}, never_scored={result.registry.never_scored}")
        print(f"By tier: {result.registry.by_tier}")
        print(f"Velocity: 24h={result.velocity.daily_24h}, 7d={result.velocity.weekly_7d}, 30d={result.velocity.monthly_30d}")
        print(f"Build backlog: staged={result.build_backlog.staged_count}, mounted={result.build_backlog.mounted_count}")

        test_conn.close()
        print("\nPASS")

    # Run the test
    asyncio.run(run_test())