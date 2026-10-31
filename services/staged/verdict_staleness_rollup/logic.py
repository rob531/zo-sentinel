from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_NAMES = ("lt_24h", "d1_7", "d7_30", "gt_30d", "never")


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def get_verdict_staleness_rollup(db: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = db.execute(select(McpServerRegistry)).scalars().all()

    buckets = {name: 0 for name in BUCKET_NAMES}
    by_verdict: dict[str, int] = {}
    by_risk_tier: dict[str, int] = {}
    unassessed: list[tuple[Any, Any, Any, Any, Any]] = []
    assessed = 0

    for row in rows:
        if row.verdict is not None:
            verdict = str(row.verdict)
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1

        if row.risk_tier is not None:
            risk_tier = str(row.risk_tier)
            by_risk_tier[risk_tier] = by_risk_tier.get(risk_tier, 0) + 1

        last_assessed = _as_utc(row.last_assessed)
        if last_assessed is None:
            buckets["never"] += 1
            unassessed.append(
                (
                    row.server_id,
                    row.name,
                    row.first_seen,
                    row.last_seen,
                    row.scan_count,
                )
            )
            continue

        assessed += 1
        age = now - last_assessed
        if age < timedelta(days=1):
            buckets["lt_24h"] += 1
        elif age <= timedelta(days=7):
            buckets["d1_7"] += 1
        elif age <= timedelta(days=30):
            buckets["d7_30"] += 1
        else:
            buckets["gt_30d"] += 1

    def first_seen_sort_key(item: tuple[Any, Any, Any, Any, Any]) -> tuple[bool, datetime]:
        first_seen = _as_utc(item[2])
        if first_seen is None:
            return (True, datetime.max.replace(tzinfo=timezone.utc))
        return (False, first_seen)

    unassessed.sort(key=first_seen_sort_key)
    oldest_unassessed = [
        {
            "server_id": item[0],
            "name": item[1],
            "first_seen": item[2],
            "last_seen": item[3],
            "scan_count": item[4],
        }
        for item in unassessed[:25]
    ]

    servers = len(rows)
    coverage = round((assessed / servers) * 100, 2) if servers else 0.0

    return {
        "buckets": buckets,
        "by_verdict": by_verdict,
        "by_risk_tier": by_risk_tier,
        "oldest_unassessed": oldest_unassessed,
        "totals": {
            "servers": servers,
            "assessed": assessed,
            "unassessed": servers - assessed,
            "assessment_coverage_pct": coverage,
        },
    }


get_verdict_staleness = get_verdict_staleness_rollup


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="assessed-now",
                    name="assessed now",
                    risk_tier="low",
                    verdict="trusted",
                    trust_score=90,
                    last_assessed=now,
                    first_seen=now - timedelta(days=100),
                    last_seen=now,
                    scan_count=10,
                ),
                McpServerRegistry(
                    server_id="assessed-3d",
                    name="assessed three days ago",
                    risk_tier="medium",
                    verdict="caution",
                    trust_score=70,
                    last_assessed=now - timedelta(days=3),
                    first_seen=now - timedelta(days=100),
                    last_seen=now,
                    scan_count=5,
                ),
                McpServerRegistry(
                    server_id="assessed-40d",
                    name="assessed forty days ago",
                    risk_tier="high",
                    verdict="blocked",
                    trust_score=20,
                    last_assessed=now - timedelta(days=40),
                    first_seen=now - timedelta(days=100),
                    last_seen=now,
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="unassessed",
                    name="never assessed",
                    risk_tier=None,
                    verdict=None,
                    trust_score=None,
                    last_assessed=None,
                    first_seen=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    last_seen=now,
                    scan_count=0,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-staleness")
    def verdict_staleness_endpoint(db: Session = Depends(get_session)):
        return get_verdict_staleness_rollup(db)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/registry/verdict-staleness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 4
    assert payload["buckets"]["never"] == 1
    assert payload["buckets"]["gt_30d"] == 1
    assert payload["oldest_unassessed"][0]["server_id"] == "unassessed"
    print("PASS")


if __name__ == "__main__":
    _self_test()