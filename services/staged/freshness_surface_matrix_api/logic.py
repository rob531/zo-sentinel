"""Logic for the freshness surface matrix API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


class SurfaceFreshness(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: Optional[float]
    age_seconds: int
    stale: bool
    payload: Dict[str, Any]


class FreshnessSurfaceMatrix(BaseModel):
    generated_at: str
    stale_after_seconds: int
    surfaces: List[SurfaceFreshness]
    total: int
    stale_count: int


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _utc_datetime(value).isoformat()


def get_freshness_surfaces(
    db: Session,
    stale_after_seconds: int = 900,
) -> FreshnessSurfaceMatrix:
    """Build the freshness matrix from the real freshness_stats table."""
    now = datetime.now(timezone.utc)
    rows = db.execute(select(FreshnessStat)).scalars().all()

    grouped: Dict[str, List[FreshnessStat]] = {}
    for row in rows:
        grouped.setdefault(row.surface, []).append(row)

    surfaces: List[SurfaceFreshness] = []
    for surface, surface_rows in grouped.items():
        newest = max(
            surface_rows,
            key=lambda row: _utc_datetime(row.computed_at),
        )
        compute_values = [
            float(row.compute_seconds)
            for row in surface_rows
            if row.compute_seconds is not None
        ]
        compute_seconds = max(compute_values) if compute_values else None
        age_seconds = int(
            (now - _utc_datetime(newest.computed_at)).total_seconds()
        )

        surfaces.append(
            SurfaceFreshness(
                surface=surface,
                computed_at=_isoformat(newest.computed_at),
                compute_seconds=compute_seconds,
                age_seconds=age_seconds,
                stale=age_seconds > stale_after_seconds,
                payload=newest.payload,
            )
        )

    surfaces.sort(key=lambda item: item.surface)
    return FreshnessSurfaceMatrix(
        generated_at=now.isoformat(),
        stale_after_seconds=stale_after_seconds,
        surfaces=surfaces,
        total=len(surfaces),
        stale_count=sum(1 for surface in surfaces if surface.stale),
    )


router = APIRouter(prefix="/api")


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfaceMatrix,
)
def freshness_surfaces(
    stale_after_seconds: int = Query(default=900, ge=0),
    db: Session = Depends(get_session),
) -> FreshnessSurfaceMatrix:
    return get_freshness_surfaces(db, stale_after_seconds)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    from datetime import timedelta

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

    test_session = TestSession()
    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            FreshnessStat(
                surface="old-surface",
                payload={"key": "old"},
                computed_at=now - timedelta(days=2),
                compute_seconds=4.5,
            ),
            FreshnessStat(
                surface="recent-surface-one",
                payload={"key": "recent-one"},
                computed_at=now - timedelta(seconds=10),
                compute_seconds=2.0,
            ),
            FreshnessStat(
                surface="recent-surface-two",
                payload={"key": "recent-two"},
                computed_at=now - timedelta(seconds=20),
                compute_seconds=3.0,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    local_app = create_app()
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["stale_count"] == 1
        assert body["surfaces"][0]["surface"] == "old-surface"
        assert {item["surface"]: item["payload"] for item in body["surfaces"]} == {
            "old-surface": {"key": "old"},
            "recent-surface-one": {"key": "recent-one"},
            "recent-surface-two": {"key": "recent-two"},
        }

    test_session.close()
    test_engine.dispose()
    print("PASS")