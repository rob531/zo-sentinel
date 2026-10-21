from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

router = APIRouter(prefix="/api")


class SurfaceFreshnessItem(BaseModel):
    surface: str
    computed_at: Optional[str] = None
    age_seconds: Optional[float] = None
    compute_seconds: Optional[float] = None
    stale: bool
    payload: Dict[str, Any]


class SurfaceFreshnessResponse(BaseModel):
    count: int
    surfaces: List[SurfaceFreshnessItem]


def _parse_payload(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {}
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_computed_at(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _query_surface_freshness(
    session: Session, stale_after_seconds: int
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        computed_at = _parse_computed_at(row.computed_at)
        age_seconds = (
            (now - computed_at).total_seconds()
            if computed_at is not None
            else None
        )
        compute_seconds = (
            float(row.compute_seconds)
            if row.compute_seconds is not None
            else None
        )
        stale = (
            computed_at is None
            or age_seconds is None
            or age_seconds > stale_after_seconds
        )
        surfaces.append(
            {
                "surface": str(row.surface),
                "computed_at": (
                    computed_at.isoformat() if computed_at is not None else None
                ),
                "age_seconds": age_seconds,
                "compute_seconds": compute_seconds,
                "stale": stale,
                "payload": _parse_payload(row.payload),
            }
        )

    return {"count": len(surfaces), "surfaces": surfaces}


def surface_freshness(
    session: Optional[Session] = None,
    stale_after_seconds: int = 3600,
) -> Dict[str, Any]:
    if session is not None:
        return _query_surface_freshness(session, stale_after_seconds)

    dependency = get_session()
    if hasattr(dependency, "__next__"):
        session = next(dependency)
        try:
            return _query_surface_freshness(session, stale_after_seconds)
        finally:
            close = getattr(dependency, "close", None)
            if close is not None:
                close()

    try:
        return _query_surface_freshness(dependency, stale_after_seconds)
    finally:
        close = getattr(dependency, "close", None)
        if close is not None:
            close()


@router.get(
    "/freshness/surfaces",
    response_model=SurfaceFreshnessResponse,
)
def get_surface_freshness(
    stale_after_seconds: int = Query(3600),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return surface_freshness(
        session=session,
        stale_after_seconds=stale_after_seconds,
    )


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            FreshnessStat(
                surface="recent",
                payload={"status": "ok", "items": 3},
                computed_at=now,
                compute_seconds=1.5,
            ),
            FreshnessStat(
                surface="stale",
                payload={"status": "old"},
                computed_at=now.replace(year=now.year - 2),
                compute_seconds=4.0,
            ),
            FreshnessStat(
                surface="missing",
                payload=None,
                computed_at=None,
                compute_seconds=None,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 3
    assert any(item["stale"] for item in body["surfaces"])
    recent = next(item for item in body["surfaces"] if item["surface"] == "recent")
    assert recent["compute_seconds"] == 1.5
    assert isinstance(recent["payload"], dict)
    for item in body["surfaces"]:
        if item["computed_at"] is not None:
            datetime.fromisoformat(item["computed_at"].replace("Z", "+00:00"))

    test_session.close()
    test_engine.dispose()
    print("PASS")