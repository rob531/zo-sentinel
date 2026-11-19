"""logic.py -- freshness surface SLA data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _band(age_seconds: float) -> str:
    if age_seconds < 3600:
        return "fresh"
    if age_seconds <= 86400:
        return "aging"
    return "stale"


def get_surface_freshness(db: Session) -> Dict[str, Any]:
    """Return the latest freshness measurement for every surface."""
    rows = db.execute(
        select(FreshnessStat).order_by(
            FreshnessStat.surface,
            FreshnessStat.computed_at.desc(),
        )
    ).scalars().all()

    latest: Dict[str, FreshnessStat] = {}
    for row in rows:
        if row.surface not in latest:
            latest[row.surface] = row

    now = _utc_now()
    surfaces: List[Dict[str, Any]] = []
    counts = {"fresh": 0, "aging": 0, "stale": 0}

    for surface in sorted(latest):
        row = latest[surface]
        computed_at = _as_utc(row.computed_at)
        age_seconds = max(0.0, float((now - computed_at).total_seconds()))
        band = _band(age_seconds)

        payload = row.payload if isinstance(row.payload, dict) else {}
        compute_seconds: Optional[float]
        if row.compute_seconds is None:
            compute_seconds = None
        else:
            compute_seconds = float(row.compute_seconds)

        surfaces.append(
            {
                "surface": str(surface),
                "computed_at": computed_at.isoformat(),
                "age_seconds": age_seconds,
                "compute_seconds": compute_seconds,
                "band": band,
                "payload_keys": [str(key) for key in payload.keys()],
            }
        )
        counts[band] += 1

    return {"surfaces": surfaces, "counts": counts}


def get_freshness_surfaces(db: Session) -> Dict[str, Any]:
    """Alias for callers that use the plural surface-freshness operation."""
    return get_surface_freshness(db)


def list_surface_freshness(db: Session) -> Dict[str, Any]:
    """Alias for callers that use a list-oriented operation name."""
    return get_surface_freshness(db)


def list_freshness_surfaces(db: Session) -> Dict[str, Any]:
    """Alias for callers that use a list-oriented surface operation name."""
    return get_surface_freshness(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="axis_scores",
                    payload={"axis": "security"},
                    computed_at=now - __import__("datetime").timedelta(seconds=60),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="registry",
                    payload={"count": 2},
                    computed_at=now - __import__("datetime").timedelta(seconds=7200),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="advisories",
                    payload={"items": []},
                    computed_at=now - __import__("datetime").timedelta(seconds=200000),
                    compute_seconds=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db: Session = Depends(get_session)):
        return get_surface_freshness(db)

    with TestClient(local_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        body = response.json()
        assert len(body["surfaces"]) == 3
        by_surface = {item["surface"]: item for item in body["surfaces"]}
        assert by_surface["axis_scores"]["band"] == "fresh"
        assert by_surface["registry"]["band"] == "aging"
        assert by_surface["advisories"]["band"] == "stale"
        assert sum(body["counts"].values()) == 3

    print("PASS")