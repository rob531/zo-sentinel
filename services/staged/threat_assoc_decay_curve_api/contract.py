from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(tags=["threat-association-decay"])


class BucketResult(BaseModel):
    bucket: str
    count: int
    avg_confidence: float


class ThreatTypeResult(BaseModel):
    threat_type: str
    count: int
    avg_confidence: float


class DecayCurveResponse(BaseModel):
    days: int
    buckets: list[BucketResult]
    by_threat_type: list[ThreatTypeResult]
    by_severity: dict[str, int]
    stale_pct: float = Field(ge=0, le=100)


def _association_table() -> sa.Table:
    return sa.table(
        "mcp_threat_associations",
        sa.column("server_id", sa.String),
        sa.column("threat_type", sa.String),
        sa.column("severity", sa.String),
        sa.column("confidence", sa.Float),
        sa.column("observed_at", sa.DateTime(timezone=True)),
        sa.column("source", sa.String),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _get_columns(session: Session, table_name: str) -> set[str]:
    try:
        bind = session.get_bind()
        return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
    except Exception:
        return set()


def _read_associations(
    session: Session, days: int, now: datetime
) -> list[dict[str, Any]]:
    table = _association_table()
    columns = _get_columns(session, "mcp_threat_associations")
    cutoff = now - timedelta(days=days)

    selected_columns = [
        table.c.server_id,
        table.c.threat_type,
        table.c.severity,
        table.c.confidence,
        table.c.observed_at,
        table.c.source,
    ]
    if "expires_at" in columns:
        selected_columns.append(sa.column("expires_at", sa.DateTime(timezone=True)))

    statement = (
        sa.select(*selected_columns)
        .where(table.c.observed_at >= cutoff)
        .order_by(table.c.observed_at.desc())
    )
    return [dict(row) for row in session.execute(statement).mappings().all()]


def _bucket_for_age(age_days: float) -> str:
    if age_days < 8:
        return "0-7d"
    if age_days < 31:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def _confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_response(
    days: int, rows: list[dict[str, Any]], now: datetime
) -> DecayCurveResponse:
    bucket_names = ["0-7d", "8-30d", "31-90d", "90d+"]
    bucket_counts = {name: 0 for name in bucket_names}
    bucket_confidence = {name: 0.0 for name in bucket_names}
    threat_counts: dict[str, int] = {}
    threat_confidence: dict[str, float] = {}
    severity_counts: dict[str, int] = {}
    stale_count = 0

    for row in rows:
        observed_at = _parse_datetime(row.get("observed_at"))
        if observed_at is None:
            continue

        age_days = max(0.0, (now - observed_at).total_seconds() / 86400)
        bucket = _bucket_for_age(age_days)
        confidence = _confidence(row.get("confidence"))

        bucket_counts[bucket] += 1
        bucket_confidence[bucket] += confidence

        threat_type = str(row.get("threat_type") or "unknown")
        threat_counts[threat_type] = threat_counts.get(threat_type, 0) + 1
        threat_confidence[threat_type] = threat_confidence.get(threat_type, 0.0) + confidence

        severity = str(row.get("severity") or "unknown")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

        expires_at = _parse_datetime(row.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            stale_count += 1

    total = sum(bucket_counts.values())
    buckets = [
        BucketResult(
            bucket=name,
            count=bucket_counts[name],
            avg_confidence=(
                round(bucket_confidence[name] / bucket_counts[name], 4)
                if bucket_counts[name]
                else 0.0
            ),
        )
        for name in bucket_names
    ]

    by_threat_type = [
        ThreatTypeResult(
            threat_type=name,
            count=threat_counts[name],
            avg_confidence=(
                round(threat_confidence[name] / threat_counts[name], 4)
                if threat_counts[name]
                else 0.0
            ),
        )
        for name in sorted(threat_counts)
    ]

    stale_pct = round((stale_count / total) * 100, 2) if total else 0.0
    return DecayCurveResponse(
        days=days,
        buckets=buckets,
        by_threat_type=by_threat_type,
        by_severity=dict(sorted(severity_counts.items())),
        stale_pct=stale_pct,
    )


@router.get(
    "/threat/assoc-decay",
    response_model=DecayCurveResponse,
    summary="Threat association decay curve",
)
def threat_association_decay(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> DecayCurveResponse:
    now = datetime.now(timezone.utc)
    rows = _read_associations(session, days, now)
    return _build_response(days, rows, now)


app = FastAPI(title="Threat Association Decay Curve API")
app.include_router(router, prefix="/api")


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
    metadata = sa.MetaData()
    associations = sa.Table(
        "mcp_threat_associations",
        metadata,
        sa.Column("server_id", sa.String),
        sa.Column("threat_type", sa.String),
        sa.Column("severity", sa.String),
        sa.Column("confidence", sa.Float),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("meta", sa.JSON),
    )
    metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        {
            "server_id": "server-a",
            "threat_type": "malware",
            "severity": "high",
            "confidence": 0.9,
            "observed_at": now,
            "source": "intel",
            "expires_at": None,
            "meta": {},
        },
        {
            "server_id": "server-b",
            "threat_type": "phishing",
            "severity": "medium",
            "confidence": 0.7,
            "observed_at": now - timedelta(days=10),
            "source": "intel",
            "expires_at": None,
            "meta": {},
        },
        {
            "server_id": "server-c",
            "threat_type": "exploit",
            "severity": "critical",
            "confidence": 0.8,
            "observed_at": now - timedelta(days=200),
            "source": "intel",
            "expires_at": None,
            "meta": {},
        },
    ]
    with engine.begin() as connection:
        connection.execute(sa.insert(associations), seeded_rows)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router, prefix="/api")
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat/assoc-decay?days=365")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["days"] == 365
        assert sum(item["count"] for item in payload["buckets"]) == len(seeded_rows)
        assert next(
            item for item in payload["buckets"] if item["bucket"] == "90d+"
        )["count"] == 1
        assert payload["by_severity"]["critical"] == 1
        assert payload["stale_pct"] == 0.0

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()