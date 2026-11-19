from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import list_freshness_surfaces

router = APIRouter(prefix="/api/freshness", tags=["freshness"])


class FreshnessSurfaceResponse(BaseModel):
    surface: str
    computed_at: str
    age_seconds: float
    compute_seconds: float
    stale: bool
    payload_summary: dict[str, Any]


class FreshnessSurfacesResponse(BaseModel):
    surfaces: list[FreshnessSurfaceResponse]


def _payload_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            import json

            payload = json.loads(payload)
        except (TypeError, ValueError):
            return {}

    if not isinstance(payload, dict):
        return {}

    summary: dict[str, Any] = {}
    for key, value in list(payload.items())[:20]:
        if value is None or isinstance(value, (str, int, float, bool)):
            summary[str(key)] = value
    return summary


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/surfaces", response_model=FreshnessSurfacesResponse)
def get_freshness_surfaces(
    db: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    result = list_freshness_surfaces(db)
    records = result.get("surfaces", []) if isinstance(result, dict) else result

    surfaces: list[FreshnessSurfaceResponse] = []
    for record in records:
        if "payload_summary" in record:
            summary = record["payload_summary"]
            if not isinstance(summary, dict):
                summary = {}
        else:
            summary = _payload_summary(record.get("payload"))

        surfaces.append(
            FreshnessSurfaceResponse(
                surface=str(record["surface"]),
                computed_at=_iso_timestamp(record["computed_at"]),
                age_seconds=float(record["age_seconds"]),
                compute_seconds=float(record["compute_seconds"]),
                stale=bool(record["stale"]),
                payload_summary={
                    str(key): value
                    for key, value in list(summary.items())[:20]
                    if value is None or isinstance(value, (str, int, float, bool))
                },
            )
        )

    return FreshnessSurfacesResponse(surfaces=surfaces)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import FreshnessStat

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="fresh",
                    payload={"max_age": 3600, "owner": "registry"},
                    computed_at=now,
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="stale",
                    payload={"max_age": 3600, "owner": "registry"},
                    computed_at=now - timedelta(days=7),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="empty",
                    payload={},
                    computed_at=now,
                    compute_seconds=0.1,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        surfaces = response.json()["surfaces"]
        assert len(surfaces) == 3
        assert next(item for item in surfaces if item["surface"] == "stale")["stale"] is True
        assert next(item for item in surfaces if item["surface"] == "fresh")["stale"] is False
        assert all("surface" in item and "age_seconds" in item for item in surfaces)

    print("PASS")