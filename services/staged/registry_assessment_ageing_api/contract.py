from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


BUCKET_NAMES = ("0-24", "24-72", "72-168", "168-720", "720+")
NEVER_ASSESSED_BUCKET = "never_assessed"


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _bucket_for_age(age_hours: float) -> str:
    if age_hours < 24:
        return "0-24"
    if age_hours < 72:
        return "24-72"
    if age_hours < 168:
        return "72-168"
    if age_hours < 720:
        return "168-720"
    return "720+"


def get_assessment_ageing(
    session=Depends(get_session),
) -> Dict[str, Any]:
    statement = (
        __import__("sqlalchemy")
        .select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.first_seen,
            McpServerRegistry.scan_count,
            McpServerRegistry.trust_score,
        )
    )
    rows = session.execute(statement).all()

    now = datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in BUCKET_NAMES}
    counts[NEVER_ASSESSED_BUCKET] = 0
    tier_counts: Dict[str, Dict[str, int]] = {
        bucket: {} for bucket in (*BUCKET_NAMES, NEVER_ASSESSED_BUCKET)
    }
    stale_count = 0

    for row in rows:
        assessed_at = _as_utc(row.last_assessed)
        if assessed_at is None:
            bucket = NEVER_ASSESSED_BUCKET
            stale_count += 1
        else:
            age_hours = max(0.0, (now - assessed_at).total_seconds() / 3600.0)
            bucket = _bucket_for_age(age_hours)
            if age_hours > 168:
                stale_count += 1

        tier = row.risk_tier if row.risk_tier is not None else "unknown"
        tier = str(tier)
        counts[bucket] += 1
        tier_counts[bucket][tier] = tier_counts[bucket].get(tier, 0) + 1

    buckets = [
        {
            "bucket": bucket,
            "count": counts[bucket],
            "tier_counts": tier_counts[bucket],
        }
        for bucket in (*BUCKET_NAMES, NEVER_ASSESSED_BUCKET)
    ]

    tier_by_bucket: List[Dict[str, Any]] = []
    for bucket in (*BUCKET_NAMES, NEVER_ASSESSED_BUCKET):
        for tier, count in tier_counts[bucket].items():
            tier_by_bucket.append({"bucket": bucket, "tier": tier, "count": count})

    return {
        "buckets": buckets,
        "tier_by_bucket": tier_by_bucket,
        "total": len(rows),
        "stale_count": stale_count,
        "never_assessed_count": counts[NEVER_ASSESSED_BUCKET],
    }


@router.get("/registry/assessment-ageing")
def assessment_ageing_endpoint(
    session=Depends(get_session),
) -> Dict[str, Any]:
    return get_assessment_ageing(session)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    last_assessed DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    first_seen DATETIME,
                    scan_count INTEGER,
                    trust_score REAL
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, verdict, risk_tier, last_assessed,
                    last_seen, last_scanned, first_seen, scan_count, trust_score
                ) VALUES (
                    :server_id, :name, :verdict, :risk_tier, :last_assessed,
                    :last_seen, :last_scanned, :first_seen, :scan_count, :trust_score
                )
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "one",
                    "verdict": "trusted",
                    "risk_tier": "low",
                    "last_assessed": now - timedelta(hours=1),
                    "last_seen": now,
                    "last_scanned": now,
                    "first_seen": now - timedelta(days=2),
                    "scan_count": 2,
                    "trust_score": 0.9,
                },
                {
                    "server_id": "server-2",
                    "name": "two",
                    "verdict": "suspect",
                    "risk_tier": "medium",
                    "last_assessed": now - timedelta(hours=100),
                    "last_seen": now,
                    "last_scanned": now,
                    "first_seen": now - timedelta(days=5),
                    "scan_count": 5,
                    "trust_score": 0.6,
                },
                {
                    "server_id": "server-3",
                    "name": "three",
                    "verdict": "unknown",
                    "risk_tier": "high",
                    "last_assessed": None,
                    "last_seen": now,
                    "last_scanned": None,
                    "first_seen": now - timedelta(days=1),
                    "scan_count": 0,
                    "trust_score": 0.1,
                },
                {
                    "server_id": "server-4",
                    "name": "four",
                    "verdict": "trusted",
                    "risk_tier": "critical",
                    "last_assessed": now - timedelta(hours=2),
                    "last_seen": now,
                    "last_scanned": now,
                    "first_seen": now - timedelta(days=2),
                    "scan_count": 2,
                    "trust_score": 0.8,
                },
                {
                    "server_id": "server-5",
                    "name": "five",
                    "verdict": "trusted",
                    "risk_tier": "low",
                    "last_assessed": now - timedelta(hours=3),
                    "last_seen": now,
                    "last_scanned": now,
                    "first_seen": now - timedelta(days=2),
                    "scan_count": 2,
                    "trust_score": 0.7,
                },
            ],
        )

    def override_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/assessment-ageing")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5
        assert payload["never_assessed_count"] == 1
        assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
        assert payload["stale_count"] >= 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()