"""Logic layer for exposing stored per-surface freshness statistics."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


def _payload_object(payload: Any) -> Dict[str, Any]:
    """Return a stored JSON payload as an object, using an empty object for null."""
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def list_freshness_surfaces(db: Session) -> Dict[str, Any]:
    """Return all stored freshness statistics ordered by surface."""
    rows = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
            FreshnessStat.payload,
        ).order_by(FreshnessStat.surface.asc())
    ).all()

    return {
        "surfaces": [
            {
                "surface": str(surface),
                "computed_at": _iso8601(computed_at),
                "compute_seconds": _seconds(compute_seconds),
                "payload": _payload_object(payload),
            }
            for surface, computed_at, compute_seconds, payload in rows
        ]
    }


def get_freshness_surface(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    """Return one stored freshness statistic, or ``None`` when it does not exist."""
    row = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
            FreshnessStat.payload,
        ).where(FreshnessStat.surface == surface)
    ).one_or_none()

    if row is None:
        return None

    row_surface, computed_at, compute_seconds, payload = row
    return {
        "surface": str(row_surface),
        "computed_at": _iso8601(computed_at),
        "compute_seconds": _seconds(compute_seconds),
        "payload": _payload_object(payload),
    }


list_surfaces = list_freshness_surfaces
get_surface = get_freshness_surface
list_surface_freshness = list_freshness_surfaces
get_surface_freshness = get_freshness_surface
get_freshness_stats = list_freshness_surfaces
get_freshness_stat = get_freshness_surface


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with local_session() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db: Session = Depends(get_session)):
        return list_freshness_surfaces(db)

    @test_app.get("/api/freshness/surfaces/{surface}")
    def freshness_surface(surface: str, db: Session = Depends(get_session)):
        result = get_freshness_surface(db, surface)
        if result is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Freshness surface not found")
        return result

    test_app.dependency_overrides[get_session] = override_get_session

    with local_session() as session:
        session.add(
            FreshnessStat(
                surface="alpha",
                payload={"age_seconds": 12},
                computed_at=datetime.utcnow(),
                compute_seconds=0.25,
            )
        )
        session.add(
            FreshnessStat(
                surface="beta",
                payload=None,
                computed_at=datetime.utcnow(),
                compute_seconds=None,
            )
        )
        session.commit()

    with TestClient(test_app) as client:
        collection_response = client.get("/api/freshness/surfaces")
        single_response = client.get("/api/freshness/surfaces/alpha")
        null_payload_response = client.get("/api/freshness/surfaces/beta")

        assert collection_response.status_code == 200
        assert single_response.status_code == 200
        assert null_payload_response.status_code == 200
        assert {
            item["surface"] for item in collection_response.json()["surfaces"]
        } == {"alpha", "beta"}
        assert null_payload_response.json()["payload"] == {}

    print("PASS")