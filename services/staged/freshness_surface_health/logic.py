from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _payload_keys(payload: Any) -> List[str]:
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())
    return []


def get_freshness_surface_health(db: Session) -> List[Dict[str, Any]]:
    """Return the most recent freshness observation for each surface."""
    latest_rows = db.execute(
        select(
            FreshnessStat.surface,
            func.max(FreshnessStat.computed_at),
        ).group_by(FreshnessStat.surface)
    ).all()
    latest_by_surface = {
        surface: computed_at
        for surface, computed_at in latest_rows
        if computed_at is not None
    }

    if not latest_by_surface:
        return []

    newest_computed_at = max(latest_by_surface.values())
    rows = db.execute(select(FreshnessStat)).scalars().all()

    selected: Dict[str, FreshnessStat] = {}
    for row in rows:
        latest = latest_by_surface.get(row.surface)
        if latest is None or row.computed_at != latest:
            continue
        current = selected.get(row.surface)
        if current is None or (
            current.computed_at == row.computed_at
            and (current.compute_seconds or 0.0) < (row.compute_seconds or 0.0)
        ):
            selected[row.surface] = row

    surfaces: List[Dict[str, Any]] = []
    for surface in sorted(selected):
        row = selected[surface]
        age_seconds = (newest_computed_at - row.computed_at).total_seconds()
        surfaces.append(
            {
                "surface": surface,
                "computed_at": _isoformat(row.computed_at),
                "age_seconds": age_seconds,
                "compute_seconds": float(row.compute_seconds or 0.0),
                "payload_keys": _payload_keys(row.payload),
            }
        )
    return surfaces


def summarize_freshness_surfaces(db: Session) -> List[Dict[str, Any]]:
    return get_freshness_surface_health(db)


def get_freshness_surfaces(db: Session) -> List[Dict[str, Any]]:
    return get_freshness_surface_health(db)


def build_freshness_surface_health(db: Session) -> Dict[str, Any]:
    """Return the complete freshness surface health response."""
    surfaces = get_freshness_surface_health(db)
    return {
        "surfaces": surfaces,
        "stale_count": sum(
            1 for surface in surfaces if surface["age_seconds"] > 0
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def freshness_surface_health(db: Session) -> Dict[str, Any]:
    return build_freshness_surface_health(db)


def __main__() -> None:
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
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            FreshnessStat(
                surface="alpha",
                payload={"a": 1, "b": 2},
                computed_at=datetime(2024, 1, 1, 12, 0, 0),
                compute_seconds=1.5,
            ),
            FreshnessStat(
                surface="beta",
                payload={"a": 3},
                computed_at=datetime(2024, 1, 1, 12, 1, 0),
                compute_seconds=2.5,
            ),
            FreshnessStat(
                surface="gamma",
                payload={"b": 4},
                computed_at=datetime(2024, 1, 1, 12, 2, 0),
                compute_seconds=3.5,
            ),
        ]
    )
    testing_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/freshness/surfaces")
    def freshness_surfaces(session: Session = Depends(get_session)):
        return build_freshness_surface_health(session)

    def override_get_session():
        yield testing_session

    that_app.dependency_overrides[get_session] = override_get_session
    try:
        response = TestClient(that_app).get("/api/freshness/surfaces")
        assert response.status_code == 200
        body = response.json()
        assert len(body["surfaces"]) == 3
        newest = next(
            surface
            for surface in body["surfaces"]
            if surface["surface"] == "gamma"
        )
        assert newest["age_seconds"] == 0
        assert "a" in next(
            surface
            for surface in body["surfaces"]
            if surface["surface"] == "alpha"
        )["payload_keys"]
    finally:
        that_app.dependency_overrides.clear()
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    __main__()