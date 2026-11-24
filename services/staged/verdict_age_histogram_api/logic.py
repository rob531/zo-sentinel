"""logic.py -- verdict age histogram data and computation layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


RISK_TIERS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)

BUCKETS = (
    "0-24h",
    "24-72h",
    "72-168h",
    "168-720h",
    "720h+",
)


class AgeHistogramResponse(BaseModel):
    as_of: str
    total: int
    buckets: List[dict]
    by_tier: List[dict]


def _bucket_for_age(age_hours: float) -> str:
    if age_hours <= 24:
        return "0-24h"
    if age_hours <= 72:
        return "24-72h"
    if age_hours <= 168:
        return "72-168h"
    if age_hours <= 720:
        return "168-720h"
    return "720h+"


def _normalise_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_age_histogram(
    db: Session, as_of: Optional[datetime] = None
) -> Dict[str, object]:
    """Compute the age histogram and risk-tier breakdown for registered servers."""
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    else:
        as_of = _normalise_datetime(as_of) or datetime.now(timezone.utc)

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
            McpServerRegistry.scan_count,
        )
    ).all()

    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    tier_counts = {tier: 0 for tier in RISK_TIERS}

    for row in rows:
        tier = row.risk_tier if row.risk_tier in tier_counts else "INSUFFICIENT"
        tier_counts[tier] += 1

        assessed_at = _normalise_datetime(row.last_assessed)
        if assessed_at is None:
            bucket = "720h+"
        else:
            age_hours = max(0.0, (as_of - assessed_at).total_seconds() / 3600.0)
            bucket = _bucket_for_age(age_hours)
        bucket_counts[bucket] += 1

    return {
        "as_of": as_of.isoformat(),
        "total": len(rows),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in BUCKETS
        ],
        "by_tier": [
            {"risk_tier": tier, "count": tier_counts[tier]}
            for tier in RISK_TIERS
        ],
    }


get_verdict_age_histogram = get_age_histogram
compute_age_histogram = get_age_histogram


router = APIRouter(prefix="/api")


@router.get("/verdicts/age-histogram", response_model=AgeHistogramResponse)
def age_histogram(db: Session = Depends(get_session)) -> Dict[str, object]:
    return get_age_histogram(db)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="age-test-1",
                    risk_tier="CAUTION_LIMITED",
                    last_assessed=now.replace(tzinfo=None),
                    last_seen=now.replace(tzinfo=None),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="age-test-2",
                    risk_tier="TRUSTED_GENERAL",
                    last_assessed=now.replace(tzinfo=None),
                    last_seen=now.replace(tzinfo=None),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="age-test-3",
                    risk_tier="TRUSTED_RESEARCH",
                    last_assessed=(now - __import__("datetime").timedelta(hours=200)).replace(
                        tzinfo=None
                    ),
                    last_seen=now.replace(tzinfo=None),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="age-test-4",
                    risk_tier="ENTERPRISE_CONTROLLED",
                    last_assessed=(now - __import__("datetime").timedelta(hours=100)).replace(
                        tzinfo=None
                    ),
                    last_seen=now.replace(tzinfo=None),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    application = FastAPI()
    application.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session

    response = TestClient(application).get("/api/verdicts/age-histogram")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["buckets"][0]["count"] == 2
    assert {"risk_tier": "CAUTION_LIMITED", "count": 1} in payload["by_tier"]
    print("PASS")


if __name__ == "__main__":
    _self_test()