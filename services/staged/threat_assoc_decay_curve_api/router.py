from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_assoc_decay_curve
except ImportError:
    try:
        from .logic import get_decay_curve as get_assoc_decay_curve
    except ImportError:
        get_assoc_decay_curve = None


router = APIRouter(prefix="/api", tags=["threat-association-decay"])


class DecayBucket(BaseModel):
    bucket: str
    count: int
    avg_confidence: float


class ThreatTypeSummary(BaseModel):
    threat_type: str
    count: int
    avg_confidence: float


class AssocDecayResponse(BaseModel):
    days: int
    buckets: list[DecayBucket]
    by_threat_type: list[ThreatTypeSummary]
    by_severity: dict[str, int]
    stale_pct: float = Field(ge=0, le=100)


_BUCKET_NAMES = ("0-7d", "8-30d", "31-90d", "90d+")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
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


def _query_decay(db: Session, days: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    try:
        columns = {
            column["name"]
            for column in inspect(db.bind).get_columns("mcp_threat_associations")
        }
    except Exception:
        columns = {
            "server_id",
            "threat_type",
            "severity",
            "confidence",
            "observed_at",
            "source",
            "expires_at",
            "meta",
        }

    selected = [
        name
        for name in (
            "server_id",
            "threat_type",
            "severity",
            "confidence",
            "observed_at",
            "source",
            "expires_at",
            "meta",
        )
        if name in columns
    ]
    if not selected:
        return {
            "days": days,
            "buckets": [
                {"bucket": bucket, "count": 0, "avg_confidence": 0.0}
                for bucket in _BUCKET_NAMES
            ],
            "by_threat_type": [],
            "by_severity": {},
            "stale_pct": 0.0,
        }

    select_columns = ", ".join(selected)
    statement = text(
        f"SELECT {select_columns} "
        "FROM mcp_threat_associations "
        "WHERE observed_at >= :cutoff"
    )
    rows = db.execute(statement, {"cutoff": cutoff}).mappings().all()

    bucket_counts = {bucket: 0 for bucket in _BUCKET_NAMES}
    bucket_confidence = {bucket: 0.0 for bucket in _BUCKET_NAMES}
    threat_counts: dict[str, int] = {}
    threat_confidence: dict[str, float] = {}
    severity_counts: dict[str, int] = {}
    stale_count = 0

    for row in rows:
        observed_at = _as_datetime(row.get("observed_at"))
        if observed_at is None:
            continue

        age_days = max(0.0, (now - observed_at).total_seconds() / 86400.0)
        if age_days <= 7:
            bucket = _BUCKET_NAMES[0]
        elif age_days <= 30:
            bucket = _BUCKET_NAMES[1]
        elif age_days <= 90:
            bucket = _BUCKET_NAMES[2]
        else:
            bucket = _BUCKET_NAMES[3]

        confidence = row.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence = 0.0

        bucket_counts[bucket] += 1
        bucket_confidence[bucket] += confidence

        threat_type = str(row.get("threat_type") or "unknown")
        threat_counts[threat_type] = threat_counts.get(threat_type, 0) + 1
        threat_confidence[threat_type] = threat_confidence.get(threat_type, 0.0) + confidence

        severity = str(row.get("severity") or "unknown")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

        expires_at = _as_datetime(row.get("expires_at"))
        if expires_at is not None and expires_at < now:
            stale_count += 1

    total = sum(bucket_counts.values())
    buckets = []
    for bucket in _BUCKET_NAMES:
        count = bucket_counts[bucket]
        buckets.append(
            {
                "bucket": bucket,
                "count": count,
                "avg_confidence": round(
                    bucket_confidence[bucket] / count if count else 0.0, 4
                ),
            }
        )

    by_threat_type = [
        {
            "threat_type": threat_type,
            "count": count,
            "avg_confidence": round(
                threat_confidence[threat_type] / count if count else 0.0, 4
            ),
        }
        for threat_type, count in sorted(
            threat_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    return {
        "days": days,
        "buckets": buckets,
        "by_threat_type": by_threat_type,
        "by_severity": severity_counts,
        "stale_pct": round((stale_count / total * 100.0) if total else 0.0, 2),
    }


@router.get(
    "/threat/assoc-decay",
    response_model=AssocDecayResponse,
    summary="Threat association age and confidence decay",
)
def assoc_decay(
    days: int = Query(default=365, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> Any:
    if get_assoc_decay_curve is not None:
        return get_assoc_decay_curve(db, days)
    return _query_decay(db, days)


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    server_id TEXT,
                    threat_type TEXT,
                    severity TEXT,
                    confidence REAL,
                    observed_at DATETIME,
                    source TEXT,
                    expires_at DATETIME,
                    meta TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_threat_associations
                    (server_id, threat_type, severity, confidence,
                     observed_at, source, expires_at, meta)
                VALUES
                    (:server_id, :threat_type, :severity, :confidence,
                     :observed_at, :source, :expires_at, :meta)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "threat_type": "malware",
                    "severity": "high",
                    "confidence": 0.95,
                    "observed_at": now,
                    "source": "test",
                    "expires_at": now + timedelta(days=1),
                    "meta": "{}",
                },
                {
                    "server_id": "server-b",
                    "threat_type": "phishing",
                    "severity": "medium",
                    "confidence": 0.75,
                    "observed_at": now - timedelta(days=10),
                    "source": "test",
                    "expires_at": None,
                    "meta": "{}",
                },
                {
                    "server_id": "server-c",
                    "threat_type": "malware",
                    "severity": "critical",
                    "confidence": 0.55,
                    "observed_at": now - timedelta(days=200),
                    "source": "test",
                    "expires_at": now - timedelta(days=1),
                    "meta": "{}",
                },
            ],
        )

    self_test_app = FastAPI()
    self_test_app.include_router(router)
    self_test_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    with TestClient(self_test_app) as client:
        response = client.get("/api/threat/assoc-decay?days=365")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 3
        assert next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "90d+"
        ) == 1

    print("PASS")