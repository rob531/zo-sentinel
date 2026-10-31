from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


class LatencyHours(BaseModel):
    median: float
    p90: float
    resolved: int


class StaleUnresolved(BaseModel):
    older_7d: int
    older_30d: int


class ResolutionLatencyResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    latency_hours: LatencyHours
    stale_unresolved: StaleUnresolved
    by_reason_category: Dict[str, int]


router = APIRouter(prefix="/api")


def _as_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text_value = str(value).strip()
        if not text_value:
            return None
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text_value)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def get_resolution_latency(session=Depends(get_session)) -> ResolutionLatencyResponse:
    rows = session.query(
        McpScoreDispute.status,
        McpScoreDispute.reason_category,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).all()

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    latencies: list[float] = []
    older_7d = 0
    older_30d = 0
    now = datetime.now(timezone.utc)

    for status, reason_category, created_at, resolved_at in rows:
        status_name = str(status) if status is not None else "unknown"
        by_status[status_name] = by_status.get(status_name, 0) + 1

        created = _as_datetime(created_at)
        resolved = _as_datetime(resolved_at)

        if resolved is None:
            if created is not None:
                age = now - created
                if age > timedelta(days=7):
                    older_7d += 1
                if age > timedelta(days=30):
                    older_30d += 1
            category_name = str(reason_category) if reason_category is not None else "unknown"
            by_reason_category[category_name] = by_reason_category.get(category_name, 0) + 1
        elif created is not None:
            latencies.append(max(0.0, (resolved - created).total_seconds() / 3600.0))

    return ResolutionLatencyResponse(
        total=len(rows),
        by_status={key: by_status[key] for key in sorted(by_status)},
        latency_hours=LatencyHours(
            median=_percentile(latencies, 0.5),
            p90=_percentile(latencies, 0.9),
            resolved=len(latencies),
        ),
        stale_unresolved=StaleUnresolved(
            older_7d=older_7d,
            older_30d=older_30d,
        ),
        by_reason_category={
            key: by_reason_category[key] for key in sorted(by_reason_category)
        },
    )


@router.get(
    "/disputes/resolution-latency",
    response_model=ResolutionLatencyResponse,
)
def resolution_latency(response: ResolutionLatencyResponse = Depends(get_resolution_latency)):
    return response


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER NOT NULL,
                    proposed_overall_risk REAL NOT NULL,
                    reason_category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(microsecond=0)
        seed_rows = [
            {
                "id": 1,
                "server_id": 101,
                "proposed_overall_risk": 0.8,
                "reason_category": "axis_mismatch",
                "status": "accepted",
                "created_at": now - timedelta(hours=2),
                "resolved_at": now,
            },
            {
                "id": 2,
                "server_id": 102,
                "proposed_overall_risk": 0.6,
                "reason_category": "source_quality",
                "status": "rejected",
                "created_at": now - timedelta(hours=24),
                "resolved_at": now,
            },
            {
                "id": 3,
                "server_id": 103,
                "proposed_overall_risk": 0.4,
                "reason_category": "model_variance",
                "status": "in_review",
                "created_at": now - timedelta(hours=72),
                "resolved_at": now,
            },
            {
                "id": 4,
                "server_id": 104,
                "proposed_overall_risk": 0.7,
                "reason_category": "axis_mismatch",
                "status": "open",
                "created_at": now - timedelta(days=45),
                "resolved_at": None,
            },
            {
                "id": 5,
                "server_id": 105,
                "proposed_overall_risk": 0.5,
                "reason_category": "source_quality",
                "status": "open",
                "created_at": now - timedelta(days=35),
                "resolved_at": None,
            },
            {
                "id": 6,
                "server_id": 106,
                "proposed_overall_risk": 0.3,
                "reason_category": "model_variance",
                "status": "pending",
                "created_at": now - timedelta(days=10),
                "resolved_at": None,
            },
        ]

        for row in seed_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_score_disputes (
                        id,
                        server_id,
                        proposed_overall_risk,
                        reason_category,
                        status,
                        created_at,
                        resolved_at
                    ) VALUES (
                        :id,
                        :server_id,
                        :proposed_overall_risk,
                        :reason_category,
                        :status,
                        :created_at,
                        :resolved_at
                    )
                    """
                ),
                row,
            )

    testing_session = TestingSession()

    def override_get_session():
        return testing_session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/disputes/resolution-latency")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        assert payload["latency_hours"]["median"] == 24.0
        assert payload["latency_hours"]["p90"] >= 24.0
        assert payload["stale_unresolved"]["older_30d"] == 2
        assert sum(payload["by_status"].values()) == 6

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()