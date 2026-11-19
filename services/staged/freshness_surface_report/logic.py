"""logic.py -- freshness surface data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_keys(payload: Any) -> List[str]:
    if isinstance(payload, Mapping):
        return sorted(str(key) for key in payload.keys())
    return []


def get_freshness_surfaces(
    db: Session, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Return the current freshness statistics without exposing payload values."""
    current_time = _as_utc(now or datetime.now(timezone.utc))
    rows = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        computed_at = _as_utc(row.computed_at)
        age_seconds = (current_time - computed_at).total_seconds()
        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": row.computed_at,
                "age_seconds": age_seconds,
                "compute_seconds": row.compute_seconds,
                "status": "ok" if age_seconds < 900 else "stale",
                "payload_keys": _payload_keys(row.payload),
            }
        )
    return surfaces


__all__ = ["get_freshness_surfaces"]


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
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
    db = SessionLocal()

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            FreshnessStat(
                surface="alpha",
                payload={"value": 1, "label": "a"},
                computed_at=now - timedelta(seconds=60),
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="beta",
                payload={"nested": {"x": 1}, "enabled": True},
                computed_at=now - timedelta(seconds=120),
                compute_seconds=0.5,
            ),
            FreshnessStat(
                surface="gamma",
                payload={"count": 3},
                computed_at=now - timedelta(hours=2),
                compute_seconds=1.0,
            ),
        ]
    )
    db.commit()

    self_test_app = FastAPI()
    self_test_app.dependency_overrides[get_session] = lambda: db

    @self_test_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db: Session = Depends(get_session)):
        surfaces = get_freshness_surfaces(db)
        return {
            "surfaces": surfaces,
            "stale_count": sum(
                surface["status"] == "stale" for surface in surfaces
            ),
            "ok_count": sum(surface["status"] == "ok" for surface in surfaces),
        }

    result = freshness_surfaces(db)
    assert len(result["surfaces"]) == 3
    assert result["stale_count"] == 1
    assert result["ok_count"] == 2
    assert all(isinstance(surface["payload_keys"], list) for surface in result["surfaces"])
    print("PASS")