"""logic.py -- the service's data/computation layer (one concern, one file).

Queries the real MCP server registry and computes freshness buckets for stale
server sightings. No model, session, or database definitions are duplicated
here; production access uses app.db and app.models directly.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_LABELS = ("0-1d", "2-7d", "8-30d", "31-90d", "90d+")
MAX_STALE_SERVERS = 100


@contextmanager
def _session_from_provider() -> Iterator[Session]:
    """Open the application session in either supported get_session form."""
    resource = get_session()
    if isinstance(resource, Session):
        try:
            yield resource
        finally:
            resource.close()
        return

    manager = contextmanager(resource)()
    with manager as db:
        yield db


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
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


def _clamp_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(365, value))


def fetch_lag_rows(days: int, db: Session | None = None) -> List[Dict[str, Any]]:
    """Fetch registry sightings that are older than the requested day window."""
    window = _clamp_days(days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window)

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
    ).where(
        McpServerRegistry.last_seen.is_not(None),
        McpServerRegistry.last_seen < cutoff,
    )

    if db is None:
        with _session_from_provider() as session:
            result = session.execute(statement).all()
    else:
        result = db.execute(statement).all()

    rows: List[Dict[str, Any]] = []
    for server_id, name, risk_tier, trust_score, last_seen, last_scanned, scan_count in result:
        seen = _as_utc(last_seen)
        scanned = _as_utc(last_scanned)
        rows.append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "trust_score": trust_score,
                "last_seen": seen.isoformat() if seen is not None else None,
                "last_scanned": scanned.isoformat() if scanned is not None else None,
                "scan_count": int(scan_count) if scan_count is not None else 0,
            }
        )
    return rows


def _bucket_for(days_since_seen: int) -> str:
    if days_since_seen <= 1:
        return "0-1d"
    if days_since_seen <= 7:
        return "2-7d"
    if days_since_seen <= 30:
        return "8-30d"
    if days_since_seen <= 90:
        return "31-90d"
    return "90d+"


def last_seen_lag_buckets(now_iso: str, rows: list[dict]) -> dict:
    """Return fixed freshness bucket counts and capped stale-server details."""
    now = _as_utc(now_iso)
    if now is None:
        now = datetime.now(timezone.utc)

    counts = {label: 0 for label in BUCKET_LABELS}
    stale_servers: List[Dict[str, Any]] = []

    for row in rows:
        seen = _as_utc(row.get("last_seen"))
        if seen is None:
            continue

        elapsed = max(0.0, (now - seen).total_seconds())
        days_since_seen = int(elapsed // 86400)
        counts[_bucket_for(days_since_seen)] += 1
        stale_servers.append(
            {
                "server_id": row.get("server_id"),
                "name": row.get("name"),
                "risk_tier": row.get("risk_tier"),
                "days_since_seen": days_since_seen,
            }
        )

    stale_servers.sort(
        key=lambda item: (
            -int(item["days_since_seen"]),
            str(item.get("server_id") or ""),
        )
    )

    return {
        "generated_at": now.isoformat(),
        "buckets": [
            {"bucket": label, "count": counts[label]}
            for label in BUCKET_LABELS
        ],
        "stale_servers": stale_servers[:MAX_STALE_SERVERS],
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    class BucketOut(BaseModel):
        bucket: str
        count: int

    class StaleServerOut(BaseModel):
        server_id: str
        name: str | None = None
        risk_tier: str | None = None
        days_since_seen: int

    class LagOut(BaseModel):
        days: int
        generated_at: str
        buckets: list[BucketOut]
        stale_servers: list[StaleServerOut]

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    seeded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh",
                    name="Fresh Server",
                    risk_tier="low",
                    trust_score=95.0,
                    last_seen=seeded_at,
                    last_scanned=seeded_at,
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="ten-day",
                    name="Ten Day Server",
                    risk_tier="medium",
                    trust_score=75.0,
                    last_seen=seeded_at - timedelta(days=10),
                    last_scanned=seeded_at - timedelta(days=10),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="two-hundred-day",
                    name="Dormant Server",
                    risk_tier="high",
                    trust_score=20.0,
                    last_seen=seeded_at - timedelta(days=200),
                    last_scanned=seeded_at - timedelta(days=200),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/servers/last-seen-lag", response_model=LagOut)
    def last_seen_lag(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> Mapping[str, Any]:
        window = _clamp_days(days)
        rows = fetch_lag_rows(window, db)
        result = last_seen_lag_buckets(datetime.now(timezone.utc).isoformat(), rows)
        return {"days": window, **result}

    with TestClient(test_app) as client:
        response = client.get("/api/servers/last-seen-lag?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert any(
            bucket["bucket"] == "90d+" and bucket["count"] == 1
            for bucket in payload["buckets"]
        ), payload
        assert payload["stale_servers"][0]["server_id"] == "two-hundred-day", payload

    print("PASS")