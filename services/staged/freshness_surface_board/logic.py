"""Logic for the freshness surface board service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


DEFAULT_SLA_SECONDS = 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(computed_at: datetime, now: datetime) -> int:
    if computed_at.tzinfo is None:
        now = now.replace(tzinfo=None)
        age = now - computed_at
    else:
        age = now - computed_at
    return max(0, int(age.total_seconds()))


def _parse_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _sla_seconds(payload: Dict[str, Any]) -> int:
    value = payload.get("sla_seconds", DEFAULT_SLA_SECONDS)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_SLA_SECONDS


def _record(stat: FreshnessStat, now: datetime) -> Dict[str, Any]:
    payload = _parse_payload(stat.payload)
    age_seconds = _age_seconds(stat.computed_at, now)
    return {
        "surface": stat.surface,
        "computed_at": stat.computed_at,
        "compute_seconds": stat.compute_seconds,
        "stale": age_seconds > _sla_seconds(payload),
        "age_seconds": age_seconds,
        "payload": payload,
    }


def list_surfaces(db: Session) -> List[Dict[str, Any]]:
    """Return freshness records for all surfaces."""
    now = _utc_now()
    stats = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()
    return [_record(stat, now) for stat in stats]


def get_surface(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    """Return one surface's freshness record, if present."""
    stat = db.execute(
        select(FreshnessStat).where(FreshnessStat.surface == surface)
    ).scalar_one_or_none()
    if stat is None:
        return None
    return _record(stat, _utc_now())


def get_freshness_board(db: Session) -> Dict[str, Any]:
    """Return the complete freshness board payload."""
    return {"surfaces": list_surfaces(db)}


def list_freshness_surfaces(db: Session) -> List[Dict[str, Any]]:
    return list_surfaces(db)


def get_freshness_surface(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    return get_surface(db, surface)


def list_freshness(db: Session) -> Dict[str, Any]:
    return get_freshness_board(db)


def get_freshness(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    return get_surface(db, surface)


def get_board(db: Session) -> Dict[str, Any]:
    return get_freshness_board(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add(
            FreshnessStat(
                surface="surface-alpha",
                payload=json.dumps({"sla_seconds": 3600}),
                computed_at=now.replace(tzinfo=None),
                compute_seconds=12,
            )
        )
        session.add(
            FreshnessStat(
                surface="surface-stale",
                payload=json.dumps({"sla_seconds": 60}),
                computed_at=(now.replace(tzinfo=None)).replace(
                    microsecond=0
                )
                - __import__("datetime").timedelta(hours=2),
            )
        )
        session.add(
            FreshnessStat(
                surface="surface-malformed",
                payload="{not-json",
                computed_at=now.replace(tzinfo=None),
            )
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness")
    def freshness_board(session=Depends(get_session)):
        return get_freshness_board(session)

    @test_app.get("/api/freshness/{surface}")
    def freshness_detail(surface: str, session=Depends(get_session)):
        record = get_surface(session, surface)
        if record is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="surface not found")
        return record

    client = TestClient(test_app)
    board_response = client.get("/api/freshness")
    assert board_response.status_code == 200
    surfaces = board_response.json()["surfaces"]
    assert len(surfaces) == 3
    assert any(
        item["surface"] == "surface-stale" and item["stale"] for item in surfaces
    )
    assert any(
        item["surface"] == "surface-malformed" and item["payload"] == {}
        for item in surfaces
    )

    detail_response = client.get("/api/freshness/surface-alpha")
    assert detail_response.status_code == 200
    assert detail_response.json()["surface"] == "surface-alpha"
    assert client.get("/api/freshness/missing").status_code == 404
    print("PASS")