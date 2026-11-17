from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["server-tenure-cohorts"])


class TenureCohort(BaseModel):
    cohort: str
    min_days: int
    max_days: Optional[int]
    server_count: int
    avg_trust_score: Optional[float]
    tier_counts: Dict[str, int]
    unscored_count: int


class TenureCohortResponse(BaseModel):
    days: int
    registry_source: Optional[str]
    generated_at: datetime
    cohorts: List[TenureCohort]


_COHORT_BOUNDS = {
    "0-7d": (0, 7),
    "8-30d": (8, 30),
    "31-90d": (31, 90),
    "91-180d": (91, 180),
    "180d+": (181, None),
}


@router.get("/servers/tenure-cohorts", response_model=TenureCohortResponse)
def get_tenure_cohorts(
    days: int = Query(90, description="Tenure analysis window in days"),
    registry_source: Optional[str] = Query(
        None, description="Optional registry source filter"
    ),
    min_scan_count: int = Query(0, ge=0, description="Minimum server scan count"),
    session: Session = Depends(get_session),
) -> TenureCohortResponse:
    days = max(1, min(365, days))
    min_scan_count = max(0, min_scan_count)

    bind = session.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "sqlite":
        tenure_days = cast(
            func.julianday(func.current_date())
            - func.julianday(McpServerRegistry.first_seen),
            Integer,
        )
    else:
        tenure_days = func.current_date() - cast(
            McpServerRegistry.first_seen, __import__("sqlalchemy").Date
        )

    tenure_days = case((tenure_days < 0, 0), else_=tenure_days)
    cohort_name = case(
        (tenure_days <= 7, "0-7d"),
        (tenure_days <= 30, "8-30d"),
        (tenure_days <= 90, "31-90d"),
        (tenure_days <= 180, "91-180d"),
        else_="180d+",
    )

    statement = select(
        cohort_name.label("cohort"),
        McpServerRegistry.risk_tier.label("risk_tier"),
        McpServerRegistry.trust_score.label("trust_score"),
    ).where(McpServerRegistry.first_seen.is_not(None))

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    statement = statement.where(McpServerRegistry.scan_count >= min_scan_count)

    rows = session.execute(statement).all()

    grouped: Dict[str, List[tuple[Optional[str], Optional[float]]]] = {}
    for row in rows:
        grouped.setdefault(row.cohort, []).append(
            (row.risk_tier, row.trust_score)
        )

    cohorts: List[TenureCohort] = []
    for name, (minimum, maximum) in _COHORT_BOUNDS.items():
        entries = grouped.get(name)
        if not entries:
            continue

        tier_counts: Dict[str, int] = {}
        trust_scores: List[float] = []

        for risk_tier, trust_score in entries:
            if risk_tier is not None:
                tier_counts[risk_tier] = tier_counts.get(risk_tier, 0) + 1
            if trust_score is not None:
                trust_scores.append(float(trust_score))

        cohorts.append(
            TenureCohort(
                cohort=name,
                min_days=minimum,
                max_days=maximum,
                server_count=len(entries),
                avg_trust_score=(
                    sum(trust_scores) / len(trust_scores)
                    if trust_scores
                    else None
                ),
                tier_counts={
                    tier: tier_counts[tier] for tier in sorted(tier_counts)
                },
                unscored_count=len(entries) - len(trust_scores),
            )
        )

    return TenureCohortResponse(
        days=days,
        registry_source=registry_source,
        generated_at=datetime.now(timezone.utc),
        cohorts=cohorts,
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded_servers = [
        McpServerRegistry(
            server_id="tenure-test-1",
            name="Tenure Test 1",
            registry_source="test",
            risk_tier="TRUSTED_GENERAL",
            trust_score=90.0,
            verdict="trusted",
            first_seen=now - timedelta(days=2),
            last_seen=now,
            last_scanned=now,
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="tenure-test-2",
            name="Tenure Test 2",
            registry_source="test",
            risk_tier="CAUTION",
            trust_score=65.0,
            verdict="caution",
            first_seen=now - timedelta(days=20),
            last_seen=now,
            last_scanned=now,
            scan_count=2,
        ),
        McpServerRegistry(
            server_id="tenure-test-3",
            name="Tenure Test 3",
            registry_source="test",
            risk_tier="TRUSTED_GENERAL",
            trust_score=None,
            verdict="unknown",
            first_seen=now - timedelta(days=60),
            last_seen=now,
            last_scanned=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="tenure-test-4",
            name="Tenure Test 4",
            registry_source="test",
            risk_tier="CAUTION",
            trust_score=40.0,
            verdict="caution",
            first_seen=now - timedelta(days=200),
            last_seen=now,
            last_scanned=now,
            scan_count=1,
        ),
    ]

    with SessionLocal() as session:
        session.add_all(seeded_servers)
        session.commit()

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/servers/tenure-cohorts")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["cohorts"]) >= 2
        assert sum(
            sum(cohort["tier_counts"].values())
            for cohort in payload["cohorts"]
        ) == len(seeded_servers)
        assert all("unscored_count" in cohort for cohort in payload["cohorts"])

    print("PASS")