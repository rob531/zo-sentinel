"""Freshness surface summary API logic."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


router = APIRouter(prefix="/api")


class FreshnessSurface(BaseModel):
    surface: str
    computed_at: datetime
    age_hours: float
    compute_seconds: float
    status: str
    payload: Any = None


class FreshnessSurfaceSummary(BaseModel):
    max_age_hours: int
    count: int
    surfaces: List[FreshnessSurface]


def _decode_payload(value: Any) -> Any:
    """Return a JSON payload unchanged, or None when it cannot be decoded."""
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def freshness_surface_summary(
    db: Session, max_age_hours: int = 24
) -> FreshnessSurfaceSummary:
    """List recorded freshness surfaces and classify them by age."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces: List[FreshnessSurface] = []
    for row in rows:
        computed_at = _as_utc(row.computed_at)
        age_hours = max(0.0, (now - computed_at).total_seconds() / 3600.0)
        surfaces.append(
            FreshnessSurface(
                surface=row.surface,
                computed_at=computed_at,
                age_hours=age_hours,
                compute_seconds=float(row.compute_seconds or 0),
                status="fresh" if age_hours <= max_age_hours else "stale",
                payload=_decode_payload(row.payload),
            )
        )

    return FreshnessSurfaceSummary(
        max_age_hours=max_age_hours,
        count=len(surfaces),
        surfaces=surfaces,
    )


@router.get("/freshness/surfaces", response_model=FreshnessSurfaceSummary)
def get_freshness_surfaces(
    max_age_hours: int = 24,
    db: Session = Depends(get_session),
) -> FreshnessSurfaceSummary:
    return freshness_surface_summary(db, max_age_hours)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as test_db:
        test_db.add_all(
            [
                FreshnessStat(
                    surface="healthy",
                    payload={"healthy": True, "value": 7},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="stale",
                    payload={"healthy": False},
                    computed_at=now - timedelta(hours=48),
                    compute_seconds=3.5,
                ),
                FreshnessStat(
                    surface="recent",
                    payload={"healthy": True},
                    computed_at=now - timedelta(hours=5),
                    compute_seconds=0.75,
                ),
            ]
        )
        test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = (
        lambda: TestingSessionLocal()
    )

    response = TestClient(test_app).get(
        "/api/freshness/surfaces", params={"max_age_hours": 24}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert sum(surface["status"] == "stale" for surface in body["surfaces"]) == 1
    healthy = next(
        surface for surface in body["surfaces"] if surface["surface"] == "healthy"
    )
    assert healthy["payload"] == {"healthy": True, "value": 7}
    print("PASS")