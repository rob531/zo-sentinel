from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class RegressedServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    first_tier: str
    last_tier: str
    tier_worsened: bool
    trust_delta: float
    observations: int


class TrustRegressionResponse(BaseModel):
    days: int
    scope: str
    regressed: List[RegressedServer]
    count: int
    tier_worsened_count: int
    mean_trust_delta: float


router = APIRouter(prefix="/api/registry")
app = FastAPI(title="Trust Score Regression API")
app.include_router(router)


TIER_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def _risk_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "low"
    if p_top >= 0.6:
        return "medium"
    if p_top >= 0.4:
        return "high"
    return "critical"


def _timestamp(value: object) -> float:
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text_value)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc).timestamp()
    return parsed.astimezone(timezone.utc).timestamp()


@router.get("/trust-regression", response_model=TrustRegressionResponse)
def get_trust_regression(session: Session = Depends(get_session)) -> TrustRegressionResponse:
    sql = text(
        """
        WITH observation_bounds AS (
            SELECT
                server_id,
                COUNT(*) AS observations,
                MIN(scored_at) AS first_scored_at,
                MAX(scored_at) AS last_scored_at
            FROM mcp_llm_axis_scores
            WHERE axis_name = :axis_name
            GROUP BY server_id
        ),
        registry_ranked AS (
            SELECT
                server_id,
                name,
                registry_source,
                trust_score,
                ROW_NUMBER() OVER (
                    PARTITION BY server_id
                    ORDER BY
                        (last_assessed IS NULL),
                        last_assessed DESC,
                        (last_seen IS NULL),
                        last_seen DESC
                ) AS registry_rank
            FROM mcp_server_registry
        )
        SELECT
            bounds.server_id,
            COALESCE(registry.name, '') AS name,
            COALESCE(registry.registry_source, '') AS registry_source,
            COALESCE(registry.trust_score, 0.0) AS trust_score,
            bounds.observations,
            bounds.first_scored_at,
            bounds.last_scored_at,
            (
                SELECT scores.p_top
                FROM mcp_llm_axis_scores AS scores
                WHERE scores.server_id = bounds.server_id
                  AND scores.axis_name = :axis_name
                  AND scores.scored_at = bounds.first_scored_at
                ORDER BY scores.scored_at ASC, scores.id ASC
                LIMIT 1
            ) AS first_p_top,
            (
                SELECT scores.p_top
                FROM mcp_llm_axis_scores AS scores
                WHERE scores.server_id = bounds.server_id
                  AND scores.axis_name = :axis_name
                  AND scores.scored_at = bounds.last_scored_at
                ORDER BY scores.scored_at DESC, scores.id DESC
                LIMIT 1
            ) AS last_p_top
        FROM observation_bounds AS bounds
        LEFT JOIN registry_ranked AS registry
          ON registry.server_id = bounds.server_id
         AND registry.registry_rank = 1
        WHERE bounds.observations >= 2
        ORDER BY bounds.server_id
        """
    )
    rows = session.execute(sql, {"axis_name": "overall_risk"}).mappings().all()

    regressed: List[RegressedServer] = []
    all_first_times: List[float] = []
    all_last_times: List[float] = []

    for row in rows:
        first_p_top = float(row["first_p_top"] or 0.0)
        last_p_top = float(row["last_p_top"] or 0.0)
        trust_score = float(row["trust_score"] or 0.0)
        first_tier = _risk_tier(first_p_top)
        last_tier = _risk_tier(last_p_top)
        tier_worsened = TIER_ORDER[last_tier] > TIER_ORDER[first_tier]
        trust_delta = trust_score - (first_p_top * 100.0)

        first_time = _timestamp(row["first_scored_at"])
        last_time = _timestamp(row["last_scored_at"])
        all_first_times.append(first_time)
        all_last_times.append(last_time)

        regressed.append(
            RegressedServer(
                server_id=str(row["server_id"]),
                name=str(row["name"] or ""),
                registry_source=str(row["registry_source"] or ""),
                first_tier=first_tier,
                last_tier=last_tier,
                tier_worsened=tier_worsened,
                trust_delta=float(trust_delta),
                observations=int(row["observations"]),
            )
        )

    if all_first_times and all_last_times:
        elapsed_seconds = max(all_last_times) - min(all_first_times)
        days = max(0, int(elapsed_seconds // 86400))
    else:
        days = 0

    tier_worsened_count = sum(item.tier_worsened for item in regressed)
    mean_trust_delta = (
        sum(item.trust_delta for item in regressed) / len(regressed)
        if regressed
        else 0.0
    )

    return TrustRegressionResponse(
        days=days,
        scope="all",
        regressed=regressed,
        count=len(regressed),
        tier_worsened_count=tier_worsened_count,
        mean_trust_delta=float(mean_trust_delta),
    )


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    registry_table = McpServerRegistry.__tablename__
    scores_table = McpLlmAxisScore.__tablename__

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {registry_table} (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    trust_score FLOAT,
                    verdict TEXT,
                    risk_tier TEXT,
                    last_assessed TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    scan_count INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {scores_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    p_top FLOAT,
                    scored_at TEXT NOT NULL
                )
                """
            )
        )

        connection.execute(
            text(
                f"""
                INSERT INTO {registry_table}
                    (server_id, name, registry_source, trust_score, verdict,
                     risk_tier, last_assessed, first_seen, last_seen, scan_count)
                VALUES
                    ('server-worsened', 'Worsened Server', 'community',
                     10.0, 'reject', 'critical', '2024-01-03T00:00:00',
                     '2024-01-01T00:00:00', '2024-01-03T00:00:00', 3),
                    ('server-improved', 'Improved Server', 'official',
                     95.0, 'approve', 'low', '2024-01-03T00:00:00',
                     '2024-01-01T00:00:00', '2024-01-03T00:00:00', 3),
                    ('server-single', 'Single Observation Server', 'community',
                     50.0, 'review', 'medium', '2024-01-03T00:00:00',
                     '2024-01-01T00:00:00', '2024-01-03T00:00:00', 1)
                """
            )
        )

        connection.execute(
            text(
                f"""
                INSERT INTO {scores_table}
                    (server_id, axis_name, p_top, scored_at)
                VALUES
                    ('server-worsened', 'overall_risk', 0.9, '2024-01-01T00:00:00'),
                    ('server-worsened', 'overall_risk', 0.2, '2024-01-03T00:00:00'),
                    ('server-improved', 'overall_risk', 0.2, '2024-01-01T00:00:00'),
                    ('server-improved', 'overall_risk', 0.9, '2024-01-03T00:00:00'),
                    ('server-single', 'overall_risk', 0.5, '2024-01-01T00:00:00')
                """
            )
        )

    def override_get_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/registry/trust-regression")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 2, payload
    assert payload["tier_worsened_count"] == 1, payload

    worsened = next(
        item for item in payload["regressed"] if item["server_id"] == "server-worsened"
    )
    assert worsened["tier_worsened"] is True, payload
    assert worsened["trust_delta"] < 0, payload

    improved = next(
        item for item in payload["regressed"] if item["server_id"] == "server-improved"
    )
    assert improved["tier_worsened"] is False, payload
    assert improved["trust_delta"] > 0, payload

    assert all(
        item["server_id"] != "server-single" for item in payload["regressed"]
    ), payload

    print("PASS")


if __name__ == "__main__":
    run()