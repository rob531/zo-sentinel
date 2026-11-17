from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry


class VerdictRegressionBucket(BaseModel):
    date: str
    total: int
    verdicts: Dict[str, int]
    risk_tiers: Dict[str, int]


class VerdictRegressionResponse(BaseModel):
    days: int
    from_date: str
    to_date: str
    buckets: List[VerdictRegressionBucket]
    deltas: Dict[str, int]
    total_scored: int


router = APIRouter(prefix="/api")
app = FastAPI()


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def get_registry_verdict_regression(db, days: int = 30) -> VerdictRegressionResponse:
    days = max(1, min(int(days), 365))
    today = date.today()
    from_date = today - timedelta(days=days - 1)
    to_date = today

    start = datetime.combine(from_date, time.min)
    end = datetime.combine(to_date + timedelta(days=1), time.min)

    from sqlalchemy import select

    statement = (
        select(
            McpServerRegistry.last_assessed,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .where(
            McpServerRegistry.last_assessed.is_not(None),
            McpServerRegistry.last_assessed >= start,
            McpServerRegistry.last_assessed < end,
        )
    )

    rows = db.execute(statement).all()
    grouped: Dict[str, Dict[str, object]] = {}

    for row in rows:
        bucket_date = _date_value(row.last_assessed).isoformat()
        bucket = grouped.setdefault(
            bucket_date,
            {
                "total": 0,
                "verdicts": {},
                "risk_tiers": {},
            },
        )

        verdict = row.verdict if row.verdict not in (None, "") else "UNKNOWN"
        risk_tier = row.risk_tier if row.risk_tier not in (None, "") else "UNSCORED"

        bucket["total"] += 1
        bucket["verdicts"][verdict] = bucket["verdicts"].get(verdict, 0) + 1
        bucket["risk_tiers"][risk_tier] = bucket["risk_tiers"].get(risk_tier, 0) + 1

    buckets = [
        VerdictRegressionBucket(
            date=bucket_date,
            total=int(values["total"]),
            verdicts=dict(values["verdicts"]),
            risk_tiers=dict(values["risk_tiers"]),
        )
        for bucket_date, values in sorted(grouped.items())
    ]

    deltas: Dict[str, int] = {}
    if buckets:
        oldest_tiers = buckets[0].risk_tiers
        newest_tiers = buckets[-1].risk_tiers
        for tier in set(oldest_tiers) | set(newest_tiers):
            deltas[tier] = newest_tiers.get(tier, 0) - oldest_tiers.get(tier, 0)

    total_scored = sum(
        1
        for row in rows
        if row.risk_tier not in (None, "")
    )

    return VerdictRegressionResponse(
        days=days,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        buckets=buckets,
        deltas=deltas,
        total_scored=total_scored,
    )


get_verdict_regression = get_registry_verdict_regression
get_registry_verdict_regression_data = get_registry_verdict_regression
compute_verdict_regression = get_registry_verdict_regression


@router.get(
    "/registry/verdict-regression",
    response_model=VerdictRegressionResponse,
)
def registry_verdict_regression(
    days: int = Query(default=30, ge=1, le=365),
    db=Depends(get_session),
) -> VerdictRegressionResponse:
    return get_registry_verdict_regression(db, days)


app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    today_value = date.today()
    yesterday_value = today_value - timedelta(days=1)

    with TestSession() as test_db:
        test_db.add_all(
            [
                McpServerRegistry(
                    server_id="regression-test-1",
                    name="Regression Test 1",
                    verdict="KNOWN_THREAT",
                    risk_tier="HIGH_RISK_ISOLATED",
                    last_assessed=datetime.combine(today_value, time(12, 0)),
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="regression-test-2",
                    name="Regression Test 2",
                    verdict="SAFE",
                    risk_tier="LOW",
                    last_assessed=datetime.combine(today_value, time(13, 0)),
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="regression-test-3",
                    name="Regression Test 3",
                    verdict="SAFE",
                    risk_tier=None,
                    last_assessed=datetime.combine(yesterday_value, time(12, 0)),
                    registry_source="test",
                ),
            ]
        )
        test_db.commit()

    def override_session():
        test_db = TestSession()
        try:
            yield test_db
        finally:
            test_db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/registry/verdict-regression",
        params={"days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["buckets"]) >= 1
    by_date = {bucket["date"]: bucket for bucket in payload["buckets"]}
    assert by_date[today_value.isoformat()]["verdicts"] == {
        "KNOWN_THREAT": 1,
        "SAFE": 1,
    }
    assert by_date[yesterday_value.isoformat()]["verdicts"] == {"SAFE": 1}
    assert "HIGH_RISK_ISOLATED" in payload["deltas"]
    print("PASS")