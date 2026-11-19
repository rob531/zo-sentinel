"""Logic for the freshness surface registry API."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


DEFAULT_MAX_AGE_SECONDS = 86400.0
MAX_PAYLOAD_SUMMARY_KEYS = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_mapping(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    return {}


def _summary_value(value: Any) -> Any:
    """Keep the summary shallow and avoid embedding nested JSON blobs."""
    if isinstance(value, Mapping):
        return "[object]"
    if isinstance(value, (list, tuple, set)):
        return "[array]"
    return value


def _payload_summary(payload: Any) -> Dict[str, Any]:
    mapping = _payload_mapping(payload)
    return {
        str(key): _summary_value(value)
        for key, value in list(mapping.items())[:MAX_PAYLOAD_SUMMARY_KEYS]
    }


def _max_age_seconds(payload: Any) -> float:
    mapping = _payload_mapping(payload)
    value = mapping.get("max_age", DEFAULT_MAX_AGE_SECONDS)
    try:
        max_age = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_SECONDS
    if not math.isfinite(max_age) or max_age < 0:
        return DEFAULT_MAX_AGE_SECONDS
    return max_age


def get_freshness_surfaces(db: Session) -> List[Dict[str, Any]]:
    """Return all freshness surfaces and their current freshness state."""
    rows = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()
    now = _utc_now()
    surfaces: List[Dict[str, Any]] = []

    for row in rows:
        computed_at = _as_utc(row.computed_at)
        age_seconds = max(0.0, (now - computed_at).total_seconds())
        payload = row.payload
        max_age = _max_age_seconds(payload)
        compute_seconds = (
            float(row.compute_seconds)
            if row.compute_seconds is not None
            else 0.0
        )

        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": computed_at.isoformat(),
                "age_seconds": age_seconds,
                "compute_seconds": compute_seconds,
                "stale": age_seconds > max_age,
                "payload_summary": _payload_summary(payload),
            }
        )

    return surfaces


# Compatibility aliases for callers using a list-oriented name.
list_freshness_surfaces = get_freshness_surfaces
get_surfaces = get_freshness_surfaces


def _self_test() -> None:
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
    FreshnessStat.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    db.add_all(
        [
            FreshnessStat(
                surface="fresh",
                payload={"max_age": 3600, "source": "test"},
                computed_at=now,
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="stale",
                payload={"max_age": 3600},
                computed_at=now.replace(year=now.year - 1),
                compute_seconds=1.5,
            ),
            FreshnessStat(
                surface="empty",
                payload={},
                computed_at=now,
                compute_seconds=0.1,
            ),
        ]
    )
    db.commit()

    def override_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/freshness/surfaces")
    def freshness_surfaces(session=__import__("fastapi").Depends(get_session)):
        return {"surfaces": get_freshness_surfaces(session)}

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    surfaces = response.json()["surfaces"]
    assert len(surfaces) == 3
    assert next(item for item in surfaces if item["surface"] == "stale")["stale"] is True
    assert next(item for item in surfaces if item["surface"] == "fresh")["stale"] is False
    assert all("surface" in item and "age_seconds" in item for item in surfaces)
    print("PASS")


if __name__ == "__main__":
    _self_test()