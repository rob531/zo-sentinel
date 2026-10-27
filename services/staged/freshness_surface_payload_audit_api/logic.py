from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


STALE_AFTER_SECONDS = 3600.0


class FreshnessSurfacePayloadAuditItem(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: float
    stale: bool
    compute_seconds: float
    key_count: int
    keys: list[str]


class FreshnessSurfacePayloadAuditResponse(BaseModel):
    surfaces: list[FreshnessSurfacePayloadAuditItem]
    total_surfaces: int


PayloadAuditSurface = FreshnessSurfacePayloadAuditItem
PayloadAuditResponse = FreshnessSurfacePayloadAuditResponse
SurfacePayloadAuditItem = FreshnessSurfacePayloadAuditItem
SurfacePayloadAuditResponse = FreshnessSurfacePayloadAuditResponse
FreshnessPayloadAuditItem = FreshnessSurfacePayloadAuditItem
FreshnessPayloadAuditResponse = FreshnessSurfacePayloadAuditResponse


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_keys(payload: Any) -> list[str]:
    value = payload
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if not isinstance(value, dict):
        return []

    return sorted(str(key) for key in value.keys())


def get_freshness_surface_payload_audit(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    statement = select(
        FreshnessStat.surface,
        FreshnessStat.computed_at,
        FreshnessStat.payload,
        FreshnessStat.compute_seconds,
    ).order_by(FreshnessStat.surface.asc())

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)
    surfaces: list[dict[str, Any]] = []

    for surface, computed_at, payload, compute_seconds in rows:
        normalized_computed_at = _utc_datetime(computed_at)
        age_seconds = (now - normalized_computed_at).total_seconds()
        keys = _payload_keys(payload)

        surfaces.append(
            {
                "surface": str(surface),
                "computed_at": computed_at,
                "age_seconds": float(age_seconds),
                "stale": age_seconds > STALE_AFTER_SECONDS,
                "compute_seconds": float(compute_seconds or 0.0),
                "key_count": len(keys),
                "keys": keys,
            }
        )

    return {
        "surfaces": surfaces,
        "total_surfaces": len(surfaces),
    }


get_freshness_surfaces_payload_audit = get_freshness_surface_payload_audit
get_freshness_payload_audit = get_freshness_surface_payload_audit
get_payload_audit = get_freshness_surface_payload_audit
get_surfaces = get_freshness_surface_payload_audit
read_payload_audit = get_freshness_surface_payload_audit
fetch_payload_audit = get_freshness_surface_payload_audit


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    FreshnessStat.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="example.com",
                    payload={"a": 1, "b": 2},
                    computed_at=now,
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="stale.example.com",
                    payload={"old": True},
                    computed_at=now.replace(tzinfo=None)
                    - __import__("datetime").timedelta(seconds=7200),
                    compute_seconds=1.5,
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/freshness/surfaces/payload-audit")
    def payload_audit_endpoint(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_freshness_surface_payload_audit(session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces/payload-audit")
        assert response.status_code == 200
        body = response.json()
        assert body["total_surfaces"] == 2

        fresh = next(
            row for row in body["surfaces"] if row["surface"] == "example.com"
        )
        stale = next(
            row
            for row in body["surfaces"]
            if row["surface"] == "stale.example.com"
        )
        assert fresh["key_count"] == 2
        assert fresh["keys"] == ["a", "b"]
        assert fresh["stale"] is False
        assert stale["stale"] is True

    print("PASS")