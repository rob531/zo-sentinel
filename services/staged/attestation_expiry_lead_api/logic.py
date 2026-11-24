"""Logic for attestation expiry lead reporting over the real data layer."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_ORDER = ("expired", "0-7", "8-30", "31-90", "90+")
_DAY_SECONDS = 24 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _attestation_table(db: Session) -> Table:
    metadata = MetaData()
    return Table(
        "mcp_attestations",
        metadata,
        autoload_with=db.get_bind(),
    )


def _bucket_for(expires_at: datetime, now: datetime) -> str | None:
    remaining_seconds = (expires_at - now).total_seconds()
    if remaining_seconds <= 0:
        return "expired"

    remaining_days = max(0, math.ceil(remaining_seconds / _DAY_SECONDS))
    if remaining_days <= 7:
        return "0-7"
    if remaining_days <= 30:
        return "8-30"
    if remaining_days <= 90:
        return "31-90"
    return "90+"


def _latest_attestations(rows: List[Any]) -> Dict[str, Any]:
    latest: Dict[str, Any] = {}
    for row in rows:
        server_id = row.attestation_server_id
        if server_id is None:
            continue
        server_id = str(server_id)

        attested_at = _as_datetime(row.attested_at)
        expires_at = _as_datetime(row.expires_at)
        if expires_at is None:
            continue

        ordering_key = (
            attested_at.timestamp() if attested_at is not None else expires_at.timestamp()
        )
        current = latest.get(server_id)
        if current is None:
            latest[server_id] = {
                "expires_at": expires_at,
                "ordering_key": ordering_key,
                "status": row.status,
                "risk_tier": row.risk_tier,
            }
            continue

        current_key = current["ordering_key"]
        if ordering_key >= current_key:
            latest[server_id] = {
                "expires_at": expires_at,
                "ordering_key": ordering_key,
                "status": row.status,
                "risk_tier": row.risk_tier,
            }
    return latest


def _build_result(db: Session, days: int) -> Dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    attestations = _attestation_table(db)
    statement = (
        select(
            attestations.c.server_id.label("attestation_server_id"),
            attestations.c.attested_at.label("attested_at"),
            attestations.c.expires_at.label("expires_at"),
            attestations.c.status.label("status"),
            McpServerRegistry.server_id.label("registry_server_id"),
            McpServerRegistry.risk_tier.label("risk_tier"),
        )
        .select_from(
            attestations.join(
                McpServerRegistry,
                McpServerRegistry.server_id == attestations.c.server_id,
            )
        )
    )

    rows = list(db.execute(statement).all())
    latest = _latest_attestations(rows)
    now = _utc_now()
    grouped: Dict[str, List[str]] = {name: [] for name in BUCKET_ORDER}

    for server_id, attestation in latest.items():
        bucket = _bucket_for(attestation["expires_at"], now)
        if bucket is not None:
            grouped[bucket].append(server_id)

    buckets = [
        {
            "bucket": name,
            "count": len(grouped[name]),
            "server_ids": sorted(grouped[name]),
        }
        for name in BUCKET_ORDER
    ]
    return {
        "buckets": buckets,
        "total": sum(item["count"] for item in buckets),
    }


def get_expiry_lead(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return the expiry-lead bucket payload for the requested report horizon."""
    return _build_result(db, days)


def calculate_expiry_lead(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name for callers using the calculation-style operation name."""
    return _build_result(db, days)


def get_expiry_buckets(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Return only the ordered expiry buckets."""
    return _build_result(db, days)["buckets"]


def get_expiry_lead_buckets(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Compatibility name for callers requesting bucket rows."""
    return get_expiry_buckets(db, days)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    attestation_metadata = MetaData()
    test_attestations = Table(
        "mcp_attestations",
        attestation_metadata,
        Column("server_id", String, primary_key=True),
        Column("attested_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("status", String, nullable=False),
    )
    test_attestations.create(test_engine)

    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()
    now = datetime.now(timezone.utc)

    test_session.add_all(
        [
            McpServerRegistry(server_id=f"server-{index}", risk_tier="medium")
            for index in range(1, 5)
        ]
    )
    test_session.flush()
    test_session.execute(
        insert(test_attestations),
        [
            {
                "server_id": "server-1",
                "attested_at": now - timedelta(days=30),
                "expires_at": now - timedelta(days=1),
                "status": "expired",
            },
            {
                "server_id": "server-2",
                "attested_at": now - timedelta(days=7),
                "expires_at": now + timedelta(days=3),
                "status": "valid",
            },
            {
                "server_id": "server-3",
                "attested_at": now - timedelta(days=10),
                "expires_at": now + timedelta(days=20),
                "status": "valid",
            },
            {
                "server_id": "server-4",
                "attested_at": now,
                "expires_at": now + timedelta(days=200),
                "status": "valid",
            },
        ],
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/attestations/expiry-lead")
    def expiry_lead_endpoint(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return {"days": days, **get_expiry_lead(db, days)}

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/attestations/expiry-lead?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert counts["expired"] == 1
        assert counts["0-7"] == 1
        assert payload["total"] >= 3

    test_session.close()
    test_engine.dispose()
    print("PASS")