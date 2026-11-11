from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry", tags=["registry"])

BUCKET_LABELS = ("0-24", "24-72", "72-168", "168-720", "720+")
NEVER_ASSESSED_BUCKET = "never_assessed"


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_for_age(hours: float) -> str:
    if hours < 24:
        return "0-24"
    if hours < 72:
        return "24-72"
    if hours < 168:
        return "72-168"
    if hours < 720:
        return "168-720"
    return "720+"


def _normalize_tier(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def compute_assessment_ageing(
    rows: Iterable[Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in BUCKET_LABELS}
    bucket_counts[NEVER_ASSESSED_BUCKET] = 0
    bucket_tiers: Dict[str, Counter] = defaultdict(Counter)

    total = 0
    stale_count = 0
    never_assessed_count = 0

    for row in rows:
        total += 1
        tier = _normalize_tier(
            row.risk_tier if hasattr(row, "risk_tier") else row["risk_tier"]
        )
        assessed_at = _as_utc_datetime(
            row.last_assessed if hasattr(row, "last_assessed") else row["last_assessed"]
        )

        if assessed_at is None:
            bucket = NEVER_ASSESSED_BUCKET
            never_assessed_count += 1
            stale_count += 1
        else:
            age_hours = (current_time - assessed_at).total_seconds() / 3600.0
            if age_hours < 0:
                age_hours = 0.0
            bucket = _bucket_for_age(age_hours)
            if age_hours >= 168:
                stale_count += 1

        bucket_counts[bucket] += 1
        bucket_tiers[bucket][tier] += 1

    buckets: List[Dict[str, Any]] = []
    for bucket in (*BUCKET_LABELS, NEVER_ASSESSED_BUCKET):
        tier_counts = bucket_tiers[bucket]
        buckets.append(
            {
                "bucket": bucket,
                "count": bucket_counts[bucket],
                "tier_counts": {
                    tier: tier_counts[tier] for tier in sorted(tier_counts)
                },
            }
        )

    tier_by_bucket: List[Dict[str, Any]] = []
    for bucket in (*BUCKET_LABELS, NEVER_ASSESSED_BUCKET):
        for tier in sorted(bucket_tiers[bucket]):
            tier_by_bucket.append(
                {
                    "bucket": bucket,
                    "tier": tier,
                    "count": bucket_tiers[bucket][tier],
                }
            )

    return {
        "buckets": buckets,
        "tier_by_bucket": tier_by_bucket,
        "total": total,
        "stale_count": stale_count,
        "never_assessed_count": never_assessed_count,
    }


@router.get("/assessment-ageing")
def get_assessment_ageing(session: Session = Depends(get_session)) -> Dict[str, Any]:
    statement = select(
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
    rows = session.execute(statement).all()
    return compute_assessment_ageing(rows)


app = FastAPI(title="Registry Assessment Ageing API")
app.include_router(router)


if __name__ == "__main__":
    from datetime import timedelta

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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_rows = [
        {
            "server_id": "one-hour",
            "name": "One Hour",
            "verdict": "approved",
            "risk_tier": "low",
            "last_assessed": now - timedelta(hours=1),
        },
        {
            "server_id": "one-hundred-hours",
            "name": "One Hundred Hours",
            "verdict": "approved",
            "risk_tier": "medium",
            "last_assessed": now - timedelta(hours=100),
        },
        {
            "server_id": "never-assessed",
            "name": "Never Assessed",
            "verdict": "unknown",
            "risk_tier": "high",
            "last_assessed": None,
        },
        {
            "server_id": "recent-low",
            "name": "Recent Low",
            "verdict": "approved",
            "risk_tier": "low",
            "last_assessed": now - timedelta(hours=2),
        },
        {
            "server_id": "recent-critical",
            "name": "Recent Critical",
            "verdict": "approved",
            "risk_tier": "critical",
            "last_assessed": now - timedelta(hours=3),
        },
    ]
    with TestSession() as session:
        session.execute(insert(McpServerRegistry), test_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/assessment-ageing")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 5
    assert payload["never_assessed_count"] == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == 5
    assert payload["stale_count"] >= 1
    print("PASS")