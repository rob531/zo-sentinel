from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from typing import Any

from app.db import get_session
from app.models import FreshnessStat


FRESH_MAX_SECONDS = 3600
AGING_MAX_SECONDS = 6 * 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _as_utc(parsed)
    raise TypeError("computed_at must be a datetime or ISO-8601 string")


def classify_staleness(age_seconds: float) -> str:
    if age_seconds <= FRESH_MAX_SECONDS:
        return "fresh"
    if age_seconds <= AGING_MAX_SECONDS:
        return "aging"
    return "stale"


def _payload_key_count(payload: Any) -> int:
    if isinstance(payload, Mapping):
        return len(payload)
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            return 0
        return len(decoded) if isinstance(decoded, Mapping) else 0
    return 0


def summarize_surface(row: FreshnessStat, now: datetime) -> dict[str, Any]:
    computed_at = _parse_timestamp(row.computed_at)
    age_seconds = max(0.0, (now - computed_at).total_seconds())
    return {
        "surface": row.surface,
        "computed_at": computed_at,
        "compute_seconds": row.compute_seconds,
        "payload_keys": _payload_key_count(row.payload),
        "age_seconds": int(age_seconds),
        "staleness": classify_staleness(age_seconds),
    }


def get_surface_status(
    db: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _as_utc(now) if now is not None else _utc_now()
    rows = db.query(FreshnessStat).order_by(FreshnessStat.surface).all()

    surfaces = [summarize_surface(row, generated_at) for row in rows]
    counts = {"fresh": 0, "aging": 0, "stale": 0}
    for surface in surfaces:
        counts[surface["staleness"]] += 1

    worst_surface = None
    if surfaces:
        worst_surface = max(
            surfaces,
            key=lambda surface: surface["age_seconds"],
        ).copy()

    return {
        "generated_at": generated_at,
        "surfaces": surfaces,
        "counts": counts,
        "worst_surface": worst_surface,
    }


def get_freshness_surface_status(
    db: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_surface_status(db, now=now)


def get_surface_statuses(
    db: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_surface_status(db, now=now)


def get_freshness_surfaces(
    db: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_surface_status(db, now=now)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={"value": 1, "kind": "test"},
                    computed_at=now - timedelta(minutes=5),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={"value": 2, "kind": "test"},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.5,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload={"value": 3, "kind": "test"},
                    computed_at=now - timedelta(hours=24),
                    compute_seconds=1.0,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db=Depends(get_session)):
        return get_surface_status(db)

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        result = response.json()
        assert len(result["surfaces"]) == 3
        assert sum(result["counts"].values()) == 3
        assert result["counts"]["stale"] == 1

    print("PASS")