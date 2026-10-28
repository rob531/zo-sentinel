from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _decode_payload_keys(payload: Any) -> list[str]:
    if isinstance(payload, Mapping):
        decoded = payload
    elif isinstance(payload, bytes):
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
    elif isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return []
    else:
        return []

    if not isinstance(decoded, Mapping):
        return []

    return [str(key) for key in decoded.keys()]


def _age_hours(computed_at: datetime, now: datetime) -> float:
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    else:
        computed_at = computed_at.astimezone(timezone.utc)

    return max(0.0, (now - computed_at).total_seconds() / 3600.0)


def get_freshness_surface_health(
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(FreshnessStat).order_by(
                FreshnessStat.surface.asc(),
                FreshnessStat.computed_at.desc(),
            )
        )
        .scalars()
        .all()
    )

    now = datetime.now(timezone.utc)
    latest_by_surface: dict[str, FreshnessStat] = {}

    for row in rows:
        if row.surface not in latest_by_surface:
            latest_by_surface[row.surface] = row

    results: list[dict[str, Any]] = []
    for surface in sorted(latest_by_surface):
        row = latest_by_surface[surface]
        results.append(
            {
                "surface": surface,
                "last_computed_at": row.computed_at,
                "age_hours": _age_hours(row.computed_at, now),
                "compute_seconds": (
                    float(row.compute_seconds)
                    if row.compute_seconds is not None
                    else None
                ),
                "payload_keys": _decode_payload_keys(row.payload),
            }
        )

    return results


get_freshness_surfaces = get_freshness_surface_health
get_surface_health = get_freshness_surface_health
get_freshness_surface_data = get_freshness_surface_health
get_surface_freshness = get_freshness_surface_health


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="axis_scores",
                    payload={"a": 1, "b": 2},
                    computed_at=now - timedelta(hours=1),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="registry_scan",
                    payload={"count": 0},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.75,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    local_app = FastAPI()

    @local_app.get("/api/freshness/surfaces")
    def freshness_surfaces(
        surfaces: list[dict[str, Any]] = Depends(get_freshness_surface_health),
    ) -> dict[str, Any]:
        return {
            "surfaces": surfaces,
            "total_surfaces": len(surfaces),
        }

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        body = response.json()
        assert body["total_surfaces"] == 2
        axis_scores = next(
            surface
            for surface in body["surfaces"]
            if surface["surface"] == "axis_scores"
        )
        assert axis_scores["payload_keys"] == ["a", "b"]
        assert axis_scores["age_hours"] >= 0

    print("PASS")