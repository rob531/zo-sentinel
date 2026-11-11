from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import Depends
from sqlalchemy import select

from app.db import get_session
from app.models import FreshnessStat


def _numeric_payload_fields(payload: Any) -> dict[str, int | float]:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return {}

    if not isinstance(payload, Mapping):
        return {}

    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def get_surface_timing(session: Any = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.compute_seconds,
            FreshnessStat.computed_at,
            FreshnessStat.payload,
        ).order_by(FreshnessStat.surface)
    ).all()

    surfaces = []
    for row in rows:
        surfaces.append(
            {
                "surface": row.surface,
                "compute_seconds": row.compute_seconds,
                "computed_at": row.computed_at,
                "payload_fields": _numeric_payload_fields(row.payload),
            }
        )

    return {
        "surfaces": surfaces,
        "total_surfaces": len(surfaces),
    }


get_freshness_surface_timing = get_surface_timing
get_surface_timings = get_surface_timing


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
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
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={"age_seconds": 12, "count": 3},
                    computed_at=datetime(2024, 1, 1, 0, 0, 0),
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={"age_seconds": 8},
                    computed_at=datetime(2024, 1, 1, 0, 1, 0),
                    compute_seconds=2.5,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/surface-timing")
    def surface_timing(result: dict[str, Any] = Depends(get_surface_timing)):
        return result

    test_app.dependency_overrides[get_session] = lambda: TestingSession()
    response = TestClient(test_app).get("/surface-timing")

    assert response.status_code == 200
    body = response.json()
    assert body["total_surfaces"] == 2
    assert any(
        surface["surface"] == "alpha" and surface["compute_seconds"] == 1.25
        for surface in body["surfaces"]
    )
    print("PASS")