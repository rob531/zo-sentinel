from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as app_models
from app.db import get_session
from app.models import McpServerRegistry

try:
    from app.models import McpLlmAxisScore
except ImportError:
    try:
        from app.models import McpLlmAxisScore as McpLlmAxisScore
    except ImportError:
        McpLlmAxisScore = next(
            model
            for model in vars(app_models).values()
            if getattr(model, "__tablename__", None) == "mcp_llm_axis_scores"
        )


class AxisScoreFreshness(BaseModel):
    axis_name: str
    fresh_count: int
    stale_count: int
    fresh_pct: float
    oldest_scored_at: Optional[datetime]
    latest_scored_at: Optional[datetime]
    distinct_model_versions: List[str]


class AxisScoreFreshnessTotals(BaseModel):
    total_servers: int
    scored_servers: int
    never_scored: int


class AxisScoreFreshnessResponse(BaseModel):
    hours: int
    generated_at: datetime
    axes: List[AxisScoreFreshness]
    totals: AxisScoreFreshnessTotals


app = FastAPI(title="Axis Score Freshness SLA")


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        parsed = datetime.fromisoformat(candidate)
    else:
        raise TypeError(f"Unsupported scored_at value: {type(value)!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _calculate_freshness_sla(session: Session, hours: int) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=hours)

    registry_rows = session.execute(
        text(
            """
            SELECT server_id
            FROM mcp_server_registry
            """
        )
    ).mappings()
    registry_server_ids = {
        str(row["server_id"])
        for row in registry_rows
        if row["server_id"] is not None
    }

    score_rows = session.execute(
        text(
            """
            SELECT
                s.server_id,
                s.axis_name,
                s.scored_at,
                s.decision_rule_version,
                s.model_version
            FROM mcp_llm_axis_scores AS s
            INNER JOIN mcp_server_registry AS r
                ON r.server_id = s.server_id
            """
        )
    ).mappings()

    scores_by_axis: Dict[str, List[Dict[str, Any]]] = {}
    scored_server_ids = set()

    for row in score_rows:
        server_id = str(row["server_id"])
        axis_name = str(row["axis_name"])
        scored_at = _as_utc_datetime(row["scored_at"])

        if scored_at is None:
            continue

        scored_server_ids.add(server_id)
        scores_by_axis.setdefault(axis_name, []).append(
            {
                "server_id": server_id,
                "scored_at": scored_at,
                "decision_rule_version": row["decision_rule_version"],
                "model_version": row["model_version"],
            }
        )

    axis_results: List[Dict[str, Any]] = []

    for axis_name in sorted(scores_by_axis):
        axis_scores = scores_by_axis[axis_name]
        latest_by_server: Dict[str, datetime] = {}

        for score in axis_scores:
            server_id = score["server_id"]
            scored_at = score["scored_at"]
            previous = latest_by_server.get(server_id)
            if previous is None or scored_at > previous:
                latest_by_server[server_id] = scored_at

        fresh_count = sum(
            1 for scored_at in latest_by_server.values() if scored_at >= cutoff
        )
        stale_count = sum(
            1 for scored_at in latest_by_server.values() if scored_at < cutoff
        )
        scored_count = fresh_count + stale_count
        fresh_pct = (
            round((fresh_count / scored_count) * 100.0, 2)
            if scored_count
            else 0.0
        )

        all_scored_at = [score["scored_at"] for score in axis_scores]
        model_versions = sorted(
            {
                str(score["model_version"])
                for score in axis_scores
                if score["model_version"] is not None
            }
        )

        axis_results.append(
            {
                "axis_name": axis_name,
                "fresh_count": fresh_count,
                "stale_count": stale_count,
                "fresh_pct": fresh_pct,
                "oldest_scored_at": min(all_scored_at) if all_scored_at else None,
                "latest_scored_at": max(all_scored_at) if all_scored_at else None,
                "distinct_model_versions": model_versions,
            }
        )

    total_servers = len(registry_server_ids)
    scored_servers = len(scored_server_ids & registry_server_ids)

    return {
        "hours": hours,
        "generated_at": generated_at,
        "axes": axis_results,
        "totals": {
            "total_servers": total_servers,
            "scored_servers": scored_servers,
            "never_scored": total_servers - scored_servers,
        },
    }


@app.get(
    "/api/scores/freshness-sla",
    response_model=AxisScoreFreshnessResponse,
)
def get_axis_score_freshness_sla(
    hours: int = Query(default=24, gt=0),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _calculate_freshness_sla(session, hours)


def _run_contract_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        with TestingSession() as session:
            session.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id VARCHAR PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        risk_tier VARCHAR,
                        last_assessed TIMESTAMP
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE mcp_llm_axis_scores (
                        server_id VARCHAR NOT NULL,
                        axis_name VARCHAR NOT NULL,
                        scored_at TIMESTAMP NOT NULL,
                        decision_rule_version VARCHAR,
                        model_version VARCHAR
                    )
                    """
                )
            )

            now = datetime.now(timezone.utc)
            recent = now - timedelta(hours=1)
            stale = now - timedelta(hours=48)

            session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, last_assessed)
                    VALUES
                        (:server_id, :name, :risk_tier, :last_assessed)
                    """
                ),
                [
                    {
                        "server_id": "server-1",
                        "name": "Server One",
                        "risk_tier": "low",
                        "last_assessed": now,
                    },
                    {
                        "server_id": "server-2",
                        "name": "Server Two",
                        "risk_tier": "high",
                        "last_assessed": stale,
                    },
                    {
                        "server_id": "server-3",
                        "name": "Server Three",
                        "risk_tier": "medium",
                        "last_assessed": now,
                    },
                ],
            )

            session.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (
                            server_id,
                            axis_name,
                            scored_at,
                            decision_rule_version,
                            model_version
                        )
                    VALUES
                        (:server_id, :axis_name, :scored_at,
                         :decision_rule_version, :model_version)
                    """
                ),
                [
                    {
                        "server_id": "server-1",
                        "axis_name": "availability",
                        "scored_at": recent,
                        "decision_rule_version": "rule-v1",
                        "model_version": "model-v1",
                    },
                    {
                        "server_id": "server-2",
                        "axis_name": "availability",
                        "scored_at": stale,
                        "decision_rule_version": "rule-v1",
                        "model_version": "model-v1",
                    },
                    {
                        "server_id": "server-3",
                        "axis_name": "integrity",
                        "scored_at": recent,
                        "decision_rule_version": "rule-v1",
                        "model_version": "model-v1",
                    },
                ],
            )
            session.commit()

            def override_get_session():
                yield session

            app.dependency_overrides[get_session] = override_get_session

            try:
                with TestClient(app) as client:
                    response = client.get(
                        "/api/scores/freshness-sla",
                        params={"hours": 24},
                    )
                    assert response.status_code == 200, response.text

                    payload = response.json()
                    assert payload["hours"] == 24
                    assert len(payload["axes"]) == 2

                    availability = next(
                        axis
                        for axis in payload["axes"]
                        if axis["axis_name"] == "availability"
                    )
                    assert availability["fresh_count"] == 1
                    assert availability["stale_count"] == 1

                    assert payload["totals"] == {
                        "total_servers": 3,
                        "scored_servers": 3,
                        "never_scored": 0,
                    }
            finally:
                app.dependency_overrides.clear()
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_contract_self_test()