from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

STALE_THRESHOLD_SECONDS = 3600


class FreshnessSurface(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: Optional[float]
    stale: bool
    payload: Dict[str, Any]


class FreshnessSurfaceCounts(BaseModel):
    total: int
    stale: int
    fresh: int


class FreshnessSurfaceCoverageResponse(BaseModel):
    generated_at: str
    stale_threshold_seconds: int
    surfaces: List[FreshnessSurface]
    counts: FreshnessSurfaceCounts


def _iso_timestamp(value: Optional[datetime] = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_freshness_surfaces(db: Session = Depends(get_session)) -> Dict[str, Any]:
    latest_computed = (
        db.query(
            FreshnessStat.surface.label("surface"),
            func.max(FreshnessStat.computed_at).label("computed_at"),
        )
        .group_by(FreshnessStat.surface)
        .subquery()
    )

    rows = (
        db.query(FreshnessStat)
        .join(
            latest_computed,
            and_(
                FreshnessStat.surface == latest_computed.c.surface,
                FreshnessStat.computed_at == latest_computed.c.computed_at,
            ),
        )
        .order_by(FreshnessStat.surface.asc())
        .all()
    )

    generated_at = datetime.now(timezone.utc)
    surfaces: List[Dict[str, Any]] = []
    stale_count = 0

    for row in rows:
        computed_at = _utc_datetime(row.computed_at)
        stale = (generated_at - computed_at).total_seconds() > STALE_THRESHOLD_SECONDS
        if stale:
            stale_count += 1

        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": _iso_timestamp(row.computed_at),
                "compute_seconds": row.compute_seconds,
                "stale": stale,
                "payload": _parse_payload(row.payload),
            }
        )

    total = len(surfaces)
    return {
        "generated_at": _iso_timestamp(generated_at),
        "stale_threshold_seconds": STALE_THRESHOLD_SECONDS,
        "surfaces": surfaces,
        "counts": {
            "total": total,
            "stale": stale_count,
            "fresh": total - stale_count,
        },
    }


router = APIRouter(prefix="/api")


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfaceCoverageResponse,
)
def freshness_surfaces(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_freshness_surfaces(db)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="scanner",
                    payload={"items": 3, "status": "ok"},
                    computed_at=now,
                    compute_seconds=0.12,
                ),
                FreshnessStat(
                    surface="scorer",
                    payload={"items": 5, "status": "ok"},
                    computed_at=now,
                    compute_seconds=0.21,
                ),
                FreshnessStat(
                    surface="intel",
                    payload={"items": 1, "status": "old"},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.09,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/freshness/surfaces")
    body = response.json()
    assert response.status_code == 200
    assert len(body["surfaces"]) == 3
    assert sum(1 for item in body["surfaces"] if item["stale"]) == 1
    print("PASS")