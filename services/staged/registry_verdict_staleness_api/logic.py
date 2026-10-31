from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_NAMES = ("never_assessed", "0_7d", "8_30d", "31_90d", "90d_plus")
_ASSESSED_BUCKETS = ("0_7d", "8_30d", "31_90d", "90d_plus")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _days_since(value: Any, now: datetime) -> int | None:
    assessed = _as_datetime(value)
    if assessed is None:
        return None
    return max(0, (now - assessed).days)


def _bucket_for(days_since: int | None) -> str:
    if days_since is None:
        return "never_assessed"
    if days_since <= 7:
        return "0_7d"
    if days_since <= 30:
        return "8_30d"
    if days_since <= 90:
        return "31_90d"
    return "90d_plus"


def _source_name(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def _tier_name(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def get_verdict_staleness(days: int = 30, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the current distribution of registry verdict assessment ages."""
    now = datetime.now(timezone.utc)

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.last_assessed,
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )
    rows = session.execute(statement).all()

    buckets = {bucket: 0 for bucket in _ASSESSED_BUCKETS}
    by_registry_source: dict[str, dict[str, int]] = {}
    by_risk_tier: dict[str, int] = {}
    records: list[dict[str, Any]] = []

    for server_id, name, last_assessed, registry_source, risk_tier in rows:
        days_since = _days_since(last_assessed, now)
        bucket = _bucket_for(days_since)
        source = _source_name(registry_source)
        tier = _tier_name(risk_tier)

        if bucket == "never_assessed":
            # Never-assessed servers are reported separately and are not part of
            # the four assessed-age buckets.
            pass
        else:
            buckets[bucket] += 1

        source_buckets = by_registry_source.setdefault(
            source, {name: 0 for name in _BUCKET_NAMES}
        )
        source_buckets[bucket] += 1
        by_risk_tier[tier] = by_risk_tier.get(tier, 0) + 1

        records.append(
            {
                "server_id": str(server_id) if server_id is not None else "",
                "name": str(name) if name is not None else "",
                "risk_tier": str(risk_tier) if risk_tier is not None else None,
                "days_since_assessed": days_since,
                "_last_assessed": _as_datetime(last_assessed),
            }
        )

    records.sort(
        key=lambda record: (
            record["_last_assessed"] is not None,
            record["_last_assessed"] or datetime.max.replace(tzinfo=timezone.utc),
        )
    )
    stalest = []
    for record in records[:20]:
        stalest.append(
            {
                "server_id": record["server_id"],
                "name": record["name"],
                "risk_tier": record["risk_tier"],
                "days_since_assessed": record["days_since_assessed"],
            }
        )

    return {
        "days": int(days),
        "total_servers": len(records),
        "never_assessed": buckets.pop("never_assessed", 0)
        if "never_assessed" in buckets
        else sum(
            1
            for record in records
            if record["days_since_assessed"] is None
        ),
        "buckets": buckets,
        "by_registry_source": by_registry_source,
        "by_risk_tier": by_risk_tier,
        "stalest": stalest,
    }


def get_registry_verdict_staleness(
    days: int = 30, session: Session = Depends(get_session)
) -> dict[str, Any]:
    return get_verdict_staleness(days=days, session=session)


def get_staleness_summary(
    days: int = 30, session: Session = Depends(get_session)
) -> dict[str, Any]:
    return get_verdict_staleness(days=days, session=session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed_session = SessionLocal()
    base_time = datetime.now(timezone.utc)
    seeded = [
        McpServerRegistry(
            server_id="age-1",
            name="one day",
            verdict="approved",
            risk_tier="low",
            confidence=0.9,
            last_assessed=base_time - __import__("datetime").timedelta(days=1),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="age-10",
            name="ten days",
            verdict="approved",
            risk_tier="medium",
            confidence=0.9,
            last_assessed=base_time - __import__("datetime").timedelta(days=10),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="age-45",
            name="forty five days",
            verdict="review",
            risk_tier="high",
            confidence=0.9,
            last_assessed=base_time - __import__("datetime").timedelta(days=45),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="age-200",
            name="two hundred days",
            verdict="blocked",
            risk_tier="critical",
            confidence=0.9,
            last_assessed=base_time - __import__("datetime").timedelta(days=200),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="never",
            name="never assessed",
            verdict="unknown",
            risk_tier=None,
            confidence=None,
            last_assessed=None,
            registry_source="test",
        ),
    ]
    seed_session.add_all(seeded)
    seed_session.commit()
    seed_session.close()

    def override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/registry/verdict-staleness")
    def verdict_staleness(days: int = 30, session: Session = Depends(get_session)):
        return get_verdict_staleness(days=days, session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/verdict-staleness?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert payload["buckets"]["0_7d"] == 1
        assert payload["buckets"]["8_30d"] == 1
        assert payload["buckets"]["31_90d"] == 1
        assert payload["buckets"]["90d_plus"] == 1
        assert payload["never_assessed"] == 1
        assert len(payload["stalest"]) == 5
        assert any(item["server_id"] == "never" for item in payload["stalest"])

    print("PASS")