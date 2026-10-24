from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter()


class TenureCohort(BaseModel):
    cohort: str
    min_days: int
    max_days: Optional[int]
    server_count: int
    avg_trust_score: Optional[float]
    tier_counts: Dict[str, int] = Field(default_factory=dict)
    unscored_count: int


class TenureCohortResponse(BaseModel):
    days: int
    registry_source: Optional[str]
    generated_at: datetime
    cohorts: List[TenureCohort]


_COHORT_DEFINITIONS = (
    ("0-7d", 0, 7),
    ("8-30d", 8, 30),
    ("31-90d", 31, 90),
    ("91-180d", 91, 180),
    ("180d+", 181, None),
)


@router.get(
    "/servers/tenure-cohorts",
    response_model=TenureCohortResponse,
)
def get_tenure_cohorts(
    days: int = Query(90),
    registry_source: Optional[str] = Query(None),
    min_scan_count: int = Query(0),
    session: Session = Depends(get_session),
) -> TenureCohortResponse:
    days = max(1, min(int(days), 365))
    min_scan_count = max(0, int(min_scan_count))

    as_of = datetime.utcnow()
    window_start = as_of - timedelta(days=days)

    parameters = {
        "window_start": window_start,
        "cutoff_7": as_of - timedelta(days=7),
        "cutoff_30": as_of - timedelta(days=30),
        "cutoff_90": as_of - timedelta(days=90),
        "cutoff_180": as_of - timedelta(days=180),
        "registry_source": registry_source,
        "min_scan_count": min_scan_count,
    }

    statement = text(
        """
        WITH bucketed AS (
            SELECT
                CASE
                    WHEN first_seen >= :cutoff_7 THEN '0-7d'
                    WHEN first_seen >= :cutoff_30 THEN '8-30d'
                    WHEN first_seen >= :cutoff_90 THEN '31-90d'
                    WHEN first_seen >= :cutoff_180 THEN '91-180d'
                    ELSE '180d+'
                END AS cohort,
                risk_tier,
                trust_score
            FROM mcp_server_registry
            WHERE first_seen IS NOT NULL
              AND first_seen >= :window_start
              AND (:registry_source IS NULL OR registry_source = :registry_source)
              AND scan_count >= :min_scan_count
        )
        SELECT
            cohort,
            risk_tier,
            COUNT(*) AS server_count,
            SUM(trust_score) AS trust_sum,
            COUNT(trust_score) AS scored_count,
            SUM(CASE WHEN trust_score IS NULL THEN 1 ELSE 0 END) AS unscored_count
        FROM bucketed
        GROUP BY cohort, risk_tier
        """
    )

    rows = session.execute(statement, parameters).mappings().all()

    aggregates: Dict[str, Dict[str, object]] = {
        name: {
            "server_count": 0,
            "trust_sum": 0.0,
            "scored_count": 0,
            "unscored_count": 0,
            "tier_counts": {},
        }
        for name, _, _ in _COHORT_DEFINITIONS
    }

    for row in rows:
        cohort = row["cohort"]
        aggregate = aggregates.setdefault(
            cohort,
            {
                "server_count": 0,
                "trust_sum": 0.0,
                "scored_count": 0,
                "unscored_count": 0,
                "tier_counts": {},
            },
        )

        server_count = int(row["server_count"] or 0)
        aggregate["server_count"] = int(aggregate["server_count"]) + server_count
        aggregate["unscored_count"] = int(aggregate["unscored_count"]) + int(
            row["unscored_count"] or 0
        )

        trust_sum = row["trust_sum"]
        if trust_sum is not None:
            aggregate["trust_sum"] = float(aggregate["trust_sum"]) + float(trust_sum)
        aggregate["scored_count"] = int(aggregate["scored_count"]) + int(
            row["scored_count"] or 0
        )

        tier = row["risk_tier"]
        if tier is not None:
            tier_counts = aggregate["tier_counts"]
            tier_counts[tier] = tier_counts.get(tier, 0) + server_count

    cohorts: List[TenureCohort] = []
    for name, min_days, max_days in _COHORT_DEFINITIONS:
        aggregate = aggregates[name]
        scored_count = int(aggregate["scored_count"])
        trust_sum = float(aggregate["trust_sum"])

        cohorts.append(
            TenureCohort(
                cohort=name,
                min_days=min_days,
                max_days=max_days,
                server_count=int(aggregate["server_count"]),
                avg_trust_score=trust_sum / scored_count if scored_count else None,
                tier_counts=dict(aggregate["tier_counts"]),
                unscored_count=int(aggregate["unscored_count"]),
            )
        )

    return TenureCohortResponse(
        days=days,
        registry_source=registry_source,
        generated_at=datetime.now(timezone.utc),
        cohorts=cohorts,
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.utcnow()
    seeded_rows = [
        {
            "server_id": "tenure-server-1",
            "name": "Tenure Server 1",
            "registry_source": "internal",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.95,
            "verdict": "approved",
            "first_seen": now - timedelta(days=2),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 4,
        },
        {
            "server_id": "tenure-server-2",
            "name": "Tenure Server 2",
            "registry_source": "internal",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.80,
            "verdict": "approved",
            "first_seen": now - timedelta(days=20),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 3,
        },
        {
            "server_id": "tenure-server-3",
            "name": "Tenure Server 3",
            "registry_source": "internal",
            "risk_tier": "HIGH_RISK",
            "trust_score": 0.25,
            "verdict": "review",
            "first_seen": now - timedelta(days=60),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 2,
        },
        {
            "server_id": "tenure-server-4",
            "name": "Tenure Server 4",
            "registry_source": "internal",
            "risk_tier": "HIGH_RISK",
            "trust_score": None,
            "verdict": "review",
            "first_seen": now - timedelta(days=200),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
        },
    ]

    with TestingSessionLocal() as session:
        session.execute(McpServerRegistry.__table__.insert(), seeded_rows)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/servers/tenure-cohorts",
            params={"days": 365, "registry_source": "internal"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["cohorts"]) >= 2
    assert sum(
        sum(cohort["tier_counts"].values())
        for cohort in payload["cohorts"]
    ) == len(seeded_rows)
    assert all("unscored_count" in cohort for cohort in payload["cohorts"])
    assert sum(cohort["unscored_count"] for cohort in payload["cohorts"]) == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()