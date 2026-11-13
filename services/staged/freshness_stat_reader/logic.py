"""logic.py -- freshness surface data/computation layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


DEFAULT_SLA_SECONDS = 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(computed_at: datetime, now: datetime) -> float:
    return max(0.0, (now - _as_utc(computed_at)).total_seconds())


def get_freshness_stats(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    now: datetime | None = None,
) -> List[Dict[str, Any]]:
    """Return every freshness surface and its SLA staleness information."""
    current_time = _as_utc(now) if now is not None else _utc_now()
    rows = db.execute(select(FreshnessStat).order_by(FreshnessStat.surface)).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        age_seconds = _age_seconds(row.computed_at, current_time)
        surfaces.append(
            {
                "surface": row.surface,
                "payload": row.payload,
                "computed_at": row.computed_at,
                "compute_seconds": row.compute_seconds,
                "age_seconds": age_seconds,
                "stale": age_seconds > sla_seconds,
            }
        )
    return surfaces


def list_freshness_surfaces(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    now: datetime | None = None,
) -> List[Dict[str, Any]]:
    """Alias for the freshness surface reader used by the API layer."""
    return get_freshness_stats(db, sla_seconds=sla_seconds, now=now)


def read_freshness_stats(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    now: datetime | None = None,
) -> List[Dict[str, Any]]:
    """Alias for callers that use the read-oriented function name."""
    return get_freshness_stats(db, sla_seconds=sla_seconds, now=now)


def __main__() -> None:
    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.models import Base

    Base.metadata.create_all(engine)
    db = TestingSession()
    now = _utc_now()
    db.add_all(
        [
            FreshnessStat(
                surface="old",
                payload={"kind": "old"},
                computed_at=now.replace(microsecond=0) - __import__("datetime").timedelta(seconds=7200),
                compute_seconds=12,
            ),
            FreshnessStat(
                surface="fresh-one",
                payload={"kind": "fresh"},
                computed_at=now.replace(microsecond=0),
                compute_seconds=4,
            ),
            FreshnessStat(
                surface="fresh-two",
                payload={"kind": "fresh"},
                computed_at=now.replace(microsecond=0),
                compute_seconds=5,
            ),
        ]
    )
    db.commit()
    db.close()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/freshness/surfaces")
    def freshness_surfaces(
        sla_seconds: int = Query(DEFAULT_SLA_SECONDS),
        session: Session = __import__("fastapi").Depends(get_session),
    ):
        surfaces = get_freshness_stats(session, sla_seconds=sla_seconds)
        return {
            "count": len(surfaces),
            "generated_at": _utc_now(),
            "surfaces": surfaces,
        }

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert sum(1 for surface in body["surfaces"] if surface["stale"]) == 1
    assert all(surface["age_seconds"] >= 0 for surface in body["surfaces"])
    print("PASS")


if __name__ == "__main__":
    __main__()