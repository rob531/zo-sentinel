from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

try:
    from .logic import get_freshness_surfaces as _logic_get_freshness_surfaces
except ImportError:
    _logic_get_freshness_surfaces = None


router = APIRouter(prefix="/api")


class FreshnessSurface(BaseModel):
    surface: str
    keys: List[str]
    computed_at: str
    compute_seconds: float
    age_days: float


class FreshnessSurfacesResponse(BaseModel):
    surfaces: List[FreshnessSurface]
    total: int
    generated_at: str


def _payload_keys(payload) -> List[str]:
    value = payload
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    return []


def _isoformat(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sort_timestamp(value):
    if isinstance(value, datetime):
        return value
    return datetime.min.replace(tzinfo=value.tzinfo) if getattr(value, "tzinfo", None) else datetime.min


def _fetch_surfaces(session: Session, surface: Optional[str]) -> List[FreshnessSurface]:
    rows = list(session.query(FreshnessStat).all())

    timestamped = [
        (row, row.computed_at)
        for row in rows
        if isinstance(row.computed_at, datetime)
    ]
    if timestamped:
        newest_computed_at = max(
            timestamp for _, timestamp in timestamped
        )
    else:
        newest_computed_at = None

    latest_by_surface: Dict[str, FreshnessStat] = {}
    for row in rows:
        if surface is not None and row.surface != surface:
            continue
        existing = latest_by_surface.get(row.surface)
        if existing is None:
            latest_by_surface[row.surface] = row
            continue
        if isinstance(row.computed_at, datetime) and isinstance(existing.computed_at, datetime):
            if row.computed_at > existing.computed_at:
                latest_by_surface[row.surface] = row
        elif existing.computed_at is None:
            latest_by_surface[row.surface] = row

    results: List[FreshnessSurface] = []
    for row in latest_by_surface.values():
        age_days = 0.0
        if newest_computed_at is not None and isinstance(row.computed_at, datetime):
            age_days = max(
                0.0,
                (newest_computed_at - row.computed_at).total_seconds() / 86400.0,
            )
        results.append(
            FreshnessSurface(
                surface=str(row.surface),
                keys=_payload_keys(row.payload),
                computed_at=_isoformat(row.computed_at),
                compute_seconds=float(row.compute_seconds or 0.0),
                age_days=float(age_days),
            )
        )

    results.sort(key=lambda item: item.surface)
    return results


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfacesResponse,
)
def get_freshness_surfaces(
    surface: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    surfaces = _fetch_surfaces(session, surface)
    return FreshnessSurfacesResponse(
        surfaces=surfaces,
        total=len(surfaces),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
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
    FreshnessStat.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    test_session = TestSession()
    test_session.add_all(
        [
            FreshnessStat(
                surface="registry",
                payload={"servers": [], "signals": {}},
                computed_at=now,
                compute_seconds=1,
            ),
            FreshnessStat(
                surface="threat_intel",
                payload={"articles": [], "topics": []},
                computed_at=now,
                compute_seconds=0.25,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return test_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    registry = next(item for item in body["surfaces"] if item["surface"] == "registry")
    assert registry["keys"] == ["servers", "signals"]
    assert isinstance(registry["compute_seconds"], float)
    print("PASS")