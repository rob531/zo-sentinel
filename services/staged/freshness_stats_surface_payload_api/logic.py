from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (TypeError, ValueError):
            return payload
    return payload


def get_freshness_surfaces(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT surface, computed_at, compute_seconds, payload
            FROM (
                SELECT
                    surface,
                    computed_at,
                    compute_seconds,
                    payload,
                    ROW_NUMBER() OVER (
                        PARTITION BY surface
                        ORDER BY computed_at DESC
                    ) AS row_num
                FROM freshness_stats
            ) ranked
            WHERE row_num = 1
            """
        )
    ).mappings().all()

    now = datetime.now(timezone.utc)
    surfaces: List[Dict[str, Any]] = []

    for row in rows:
        computed_at = row["computed_at"]
        if isinstance(computed_at, str):
            computed_at = datetime.fromisoformat(computed_at)
        age_seconds = max(
            0.0,
            (now - _as_utc(computed_at)).total_seconds(),
        )
        surfaces.append(
            {
                "surface": row["surface"],
                "computed_at": computed_at,
                "age_seconds": age_seconds,
                "compute_seconds": row["compute_seconds"],
                "payload": _decode_payload(row["payload"]),
            }
        )

    surfaces.sort(key=lambda item: item["age_seconds"], reverse=True)
    return surfaces


# Compatibility aliases for routers and staged callers using the service's
# common naming variants.
get_freshness_surface_payloads = get_freshness_surfaces
get_surface_freshness = get_freshness_surfaces
get_freshness_stats = get_freshness_surfaces
get_freshness_surface_data = get_freshness_surfaces


def _self_test() -> None:
    from datetime import timedelta

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    session.add_all(
        [
            FreshnessStat(
                surface="alpha",
                payload={"value": 1},
                computed_at=now - timedelta(seconds=30),
                compute_seconds=1.25,
            ),
            FreshnessStat(
                surface="beta",
                payload={"value": 2},
                computed_at=now - timedelta(seconds=10),
                compute_seconds=2.5,
            ),
            FreshnessStat(
                surface="gamma",
                payload={"value": 3},
                computed_at=now - timedelta(seconds=20),
                compute_seconds=3.75,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness/surfaces")
    def freshness_surfaces(surfaces=Depends(get_freshness_surfaces)):
        return {"surfaces": surfaces}

    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert set(item["surface"] for item in body["surfaces"]) == {
        "alpha",
        "beta",
        "gamma",
    }
    ages = [item["age_seconds"] for item in body["surfaces"]]
    assert ages == sorted(ages, reverse=True)
    assert any(item["compute_seconds"] == 1.25 for item in body["surfaces"])
    print("PASS")


if __name__ == "__main__":
    _self_test()