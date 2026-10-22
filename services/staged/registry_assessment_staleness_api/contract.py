from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = ("never", "0-1d", "1-7d", "7-30d", "30-90d", "90d+")


class BucketResponse(BaseModel):
    bucket: str
    count: int
    by_tier: Dict[str, int]
    by_source: Dict[str, int]


class OldestServerResponse(BaseModel):
    server_id: str
    name: str
    last_assessed: Optional[str]


class AssessmentStalenessResponse(BaseModel):
    generated_at: str
    total_servers: int
    never_assessed: int
    buckets: List[BucketResponse]
    oldest: List[OldestServerResponse]
    median_age_days: Optional[float]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, (now - _as_utc(value)).total_seconds() / 86400.0)


def _bucket_for_age(age_days: Optional[float]) -> str:
    if age_days is None:
        return "never"
    if age_days < 1:
        return "0-1d"
    if age_days < 7:
        return "1-7d"
    if age_days < 30:
        return "7-30d"
    if age_days < 90:
        return "30-90d"
    return "90d+"


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def build_assessment_staleness(session: Session) -> AssessmentStalenessResponse:
    now = datetime.now(timezone.utc)
    rows = session.execute(select(McpServerRegistry)).scalars().all()

    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    bucket_tiers = {bucket: {} for bucket in BUCKETS}
    bucket_sources = {bucket: {} for bucket in BUCKETS}
    ages: List[float] = []
    oldest: List[OldestServerResponse] = []
    never_assessed = 0

    for row in rows:
        age = _age_days(row.last_assessed, now)
        bucket = _bucket_for_age(age)
        bucket_counts[bucket] += 1

        tier = row.risk_tier or "unknown"
        source = row.registry_source or "unknown"
        bucket_tiers[bucket][tier] = bucket_tiers[bucket].get(tier, 0) + 1
        bucket_sources[bucket][source] = bucket_sources[bucket].get(source, 0) + 1

        if age is None:
            never_assessed += 1
        else:
            ages.append(age)
            oldest.append(
                OldestServerResponse(
                    server_id=str(row.server_id),
                    name=str(row.name),
                    last_assessed=_as_utc(row.last_assessed).isoformat(),
                )
            )

    oldest.sort(key=lambda item: item.last_assessed or "", reverse=True)
    median_age_days = median(ages) if ages else None
    _percentile(ages, 0.5)

    return AssessmentStalenessResponse(
        generated_at=now.isoformat(),
        total_servers=len(rows),
        never_assessed=never_assessed,
        buckets=[
            BucketResponse(
                bucket=bucket,
                count=bucket_counts[bucket],
                by_tier=bucket_tiers[bucket],
                by_source=bucket_sources[bucket],
            )
            for bucket in BUCKETS
        ],
        oldest=oldest,
        median_age_days=median_age_days,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/registry/assessment-staleness",
    response_model=AssessmentStalenessResponse,
)
def registry_assessment_staleness(
    session: Session = Depends(get_session),
) -> AssessmentStalenessResponse:
    return build_assessment_staleness(session)


app = FastAPI(title="Registry Assessment Staleness API")
app.include_router(router)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        McpServerRegistry(
            server_id="never-assessed",
            name="Never Assessed",
            risk_tier="critical",
            verdict="unknown",
            last_assessed=None,
            first_seen=now - timedelta(days=40),
            last_seen=now - timedelta(days=1),
            scan_count=0,
            registry_source="github",
        ),
        McpServerRegistry(
            server_id="fresh",
            name="Fresh Server",
            risk_tier="low",
            verdict="approved",
            last_assessed=now - timedelta(hours=6),
            first_seen=now - timedelta(days=5),
            last_seen=now - timedelta(hours=1),
            scan_count=8,
            registry_source="gitlab",
        ),
        McpServerRegistry(
            server_id="stale",
            name="Stale Server",
            risk_tier="medium",
            verdict="review",
            last_assessed=now - timedelta(days=10),
            first_seen=now - timedelta(days=60),
            last_seen=now - timedelta(days=2),
            scan_count=5,
            registry_source="manual",
        ),
        McpServerRegistry(
            server_id="old",
            name="Old Server",
            risk_tier="high",
            verdict="blocked",
            last_assessed=now - timedelta(days=120),
            first_seen=now - timedelta(days=200),
            last_seen=now - timedelta(days=5),
            scan_count=3,
            registry_source="registry",
        ),
        McpServerRegistry(
            server_id="recent",
            name="Recent Server",
            risk_tier="low",
            verdict="approved",
            last_assessed=now - timedelta(days=2),
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(hours=2),
            scan_count=6,
            registry_source="github",
        ),
        McpServerRegistry(
            server_id="very-old",
            name="Very Old Server",
            risk_tier="critical",
            verdict="blocked",
            last_assessed=now - timedelta(days=200),
            first_seen=now - timedelta(days=300),
            last_seen=now - timedelta(days=8),
            scan_count=2,
            registry_source="gitlab",
        ),
    ]

    with session_factory() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/assessment-staleness")
            assert response.status_code == 200
            payload = response.json()

            assert payload["total_servers"] == 6
            assert payload["never_assessed"] == 1
            assert set(payload) >= {
                "generated_at",
                "total_servers",
                "never_assessed",
                "buckets",
                "oldest",
                "median_age_days",
            }
            assert any(
                bucket["bucket"] == "7-30d" and bucket["count"] >= 1
                for bucket in payload["buckets"]
            )
            assert all(
                set(bucket) >= {"bucket", "count", "by_tier", "by_source"}
                for bucket in payload["buckets"]
            )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()