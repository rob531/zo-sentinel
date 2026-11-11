from datetime import date, datetime, timedelta, timezone
from math import ceil, isfinite
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int
    pct: float


class TrustHistogramBucket(BaseModel):
    lo: float
    hi: float
    count: int


class StalenessBucket(BaseModel):
    bucket: str
    count: int


class RegistryRiskBandResponse(BaseModel):
    tiers: List[RiskTierCount]
    trust_histogram: List[TrustHistogramBucket]
    staleness: List[StalenessBucket]
    total: int
    generated_at: str


router = APIRouter(prefix="/api")


@router.get(
    "/registry/risk-bands",
    response_model=RegistryRiskBandResponse,
)
def get_registry_risk_bands(
    source: Optional[str] = Query(default=None),
    bucket_width: int = Query(default=10, ge=1, le=25),
    db: Session = Depends(get_session),
) -> RegistryRiskBandResponse:
    columns = (
        McpServerRegistry.registry_source,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed,
    )
    statement = select(*columns)
    if source is not None:
        statement = statement.where(McpServerRegistry.registry_source == source)

    rows = db.execute(statement).all()
    now = datetime.now(timezone.utc)

    tier_counts: Dict[str, int] = {}
    histogram_counts: Dict[int, int] = {}
    staleness_counts: Dict[str, int] = {
        "<24h": 0,
        "1-7d": 0,
        ">7d": 0,
        "null": 0,
    }

    bucket_count = ceil(100 / bucket_width)

    for row in rows:
        risk_tier = row.risk_tier
        tier = str(risk_tier).strip() if risk_tier is not None else ""
        if not tier:
            tier = "UNSCORED"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        trust_score = row.trust_score
        if trust_score is not None:
            score = float(trust_score)
            if isfinite(score):
                score = min(100.0, max(0.0, score))
                index = min(int(score // bucket_width), bucket_count - 1)
                histogram_counts[index] = histogram_counts.get(index, 0) + 1

        last_assessed = row.last_assessed
        if last_assessed is None:
            staleness_counts["null"] += 1
            continue

        if isinstance(last_assessed, str):
            try:
                last_assessed = datetime.fromisoformat(
                    last_assessed.replace("Z", "+00:00")
                )
            except ValueError:
                staleness_counts["null"] += 1
                continue
        elif isinstance(last_assessed, date) and not isinstance(
            last_assessed, datetime
        ):
            last_assessed = datetime.combine(last_assessed, datetime.min.time())

        if last_assessed.tzinfo is None:
            last_assessed = last_assessed.replace(tzinfo=timezone.utc)
        age = now - last_assessed

        if age < timedelta(days=1):
            staleness_counts["<24h"] += 1
        elif age <= timedelta(days=7):
            staleness_counts["1-7d"] += 1
        else:
            staleness_counts[">7d"] += 1

    total = len(rows)
    tiers = [
        RiskTierCount(
            risk_tier=tier,
            count=count,
            pct=round((count / total) * 100.0, 2) if total else 0.0,
        )
        for tier, count in sorted(tier_counts.items())
    ]

    trust_histogram = []
    for index in range(bucket_count):
        lo = float(index * bucket_width)
        hi = float(min((index + 1) * bucket_width, 100))
        trust_histogram.append(
            TrustHistogramBucket(
                lo=lo,
                hi=hi,
                count=histogram_counts.get(index, 0),
            )
        )

    staleness = [
        StalenessBucket(bucket=bucket, count=count)
        for bucket, count in (
            ("<24h", staleness_counts["<24h"]),
            ("1-7d", staleness_counts["1-7d"]),
            (">7d", staleness_counts[">7d"]),
            ("null", staleness_counts["null"]),
        )
    ]

    return RegistryRiskBandResponse(
        tiers=tiers,
        trust_histogram=trust_histogram,
        staleness=staleness,
        total=total,
        generated_at=now.isoformat(),
    )


app = FastAPI(title="Registry Risk Band Histogram API")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    risk_tier TEXT,
                    confidence REAL,
                    last_assessed DATETIME,
                    last_seen DATETIME,
                    scan_count INTEGER
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    registry_source,
                    trust_score,
                    verdict,
                    risk_tier,
                    confidence,
                    last_assessed,
                    last_seen,
                    scan_count
                ) VALUES (
                    :server_id,
                    :name,
                    :registry_source,
                    :trust_score,
                    :verdict,
                    :risk_tier,
                    :confidence,
                    :last_assessed,
                    :last_seen,
                    :scan_count
                )
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Server One",
                    "registry_source": "github",
                    "trust_score": 15.0,
                    "verdict": "BLOCK",
                    "risk_tier": "HIGH_RISK",
                    "confidence": 0.9,
                    "last_assessed": now,
                    "last_seen": now,
                    "scan_count": 3,
                },
                {
                    "server_id": "server-2",
                    "name": "Server Two",
                    "registry_source": "github",
                    "trust_score": 25.0,
                    "verdict": "BLOCK",
                    "risk_tier": "HIGH_RISK",
                    "confidence": 0.8,
                    "last_assessed": now - timedelta(days=2),
                    "last_seen": now,
                    "scan_count": 2,
                },
                {
                    "server_id": "server-3",
                    "name": "Server Three",
                    "registry_source": "github",
                    "trust_score": 42.0,
                    "verdict": "ALLOW",
                    "risk_tier": "TRUSTED_GENERAL",
                    "confidence": 0.95,
                    "last_assessed": now,
                    "last_seen": now,
                    "scan_count": 5,
                },
                {
                    "server_id": "server-4",
                    "name": "Server Four",
                    "registry_source": "github",
                    "trust_score": 87.0,
                    "verdict": "ALLOW",
                    "risk_tier": "TRUSTED_GENERAL",
                    "confidence": 0.97,
                    "last_assessed": now - timedelta(days=8),
                    "last_seen": now,
                    "scan_count": 4,
                },
                {
                    "server_id": "server-5",
                    "name": "Server Five",
                    "registry_source": "github",
                    "trust_score": 95.0,
                    "verdict": "ALLOW",
                    "risk_tier": None,
                    "confidence": 0.7,
                    "last_assessed": now,
                    "last_seen": now,
                    "scan_count": 1,
                },
                {
                    "server_id": "server-6",
                    "name": "Server Six",
                    "registry_source": "github",
                    "trust_score": 100.0,
                    "verdict": "ALLOW",
                    "risk_tier": "",
                    "confidence": 0.6,
                    "last_assessed": None,
                    "last_seen": now,
                    "scan_count": 1,
                },
            ],
        )

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/registry/risk-bands")
            assert response.status_code == 200
            payload = response.json()

            tier_counts = {
                item["risk_tier"]: item["count"] for item in payload["tiers"]
            }
            assert tier_counts == {
                "HIGH_RISK": 2,
                "TRUSTED_GENERAL": 2,
                "UNSCORED": 2,
            }
            assert sum(
                bucket["count"] for bucket in payload["trust_histogram"]
            ) == 6
            assert payload["total"] == 6
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()