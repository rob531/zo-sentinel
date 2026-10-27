from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


class FreshnessPayloadAgeItem(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: float
    age_hours: float
    payload_bytes: int


class FreshnessPayloadAgeResponse(BaseModel):
    surfaces: list[FreshnessPayloadAgeItem]
    surface_count: int
    stale_surface_count: int
    generated_at: str


router = APIRouter(prefix="/api", tags=["freshness"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _payload_bytes(payload: Any) -> int:
    return len(json.dumps(payload).encode("utf-8"))


@router.get(
    "/freshness/payload-age",
    response_model=FreshnessPayloadAgeResponse,
    summary="Report payload freshness and size by surface",
)
def get_freshness_payload_age(
    session: Session = Depends(get_session),
) -> FreshnessPayloadAgeResponse:
    now = datetime.now(timezone.utc)
    statement = (
        select(
            FreshnessStat.surface,
            FreshnessStat.payload,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
        .order_by(
            FreshnessStat.surface.asc(),
            FreshnessStat.computed_at.desc(),
        )
    )

    newest_rows: dict[str, Any] = {}
    for row in session.execute(statement):
        if row.surface not in newest_rows:
            newest_rows[row.surface] = row

    surfaces: list[FreshnessPayloadAgeItem] = []
    for surface, row in newest_rows.items():
        computed_at = _as_utc(row.computed_at)
        age_hours = (now - computed_at).total_seconds() / 3600.0
        surfaces.append(
            FreshnessPayloadAgeItem(
                surface=surface,
                computed_at=_iso8601(row.computed_at),
                compute_seconds=float(row.compute_seconds),
                age_hours=age_hours,
                payload_bytes=_payload_bytes(row.payload),
            )
        )

    return FreshnessPayloadAgeResponse(
        surfaces=surfaces,
        surface_count=len(surfaces),
        stale_surface_count=sum(item.age_hours > 24.0 for item in surfaces),
        generated_at=now.isoformat(),
    )


app = FastAPI(title="Freshness Payload Age API")
app.include_router(router)


if __name__ == "__main__":
    from datetime import timedelta

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

    test_now = datetime.now(timezone.utc).replace(microsecond=0)
    with TestSession() as test_session:
        test_session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={"value": "fresh"},
                    computed_at=test_now - timedelta(hours=1),
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={"value": "stale"},
                    computed_at=test_now - timedelta(hours=48),
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload={},
                    computed_at=test_now - timedelta(hours=2),
                    compute_seconds=0.5,
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/payload-age")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["surface_count"] == 3, body
        assert body["stale_surface_count"] == 1, body
        gamma = next(item for item in body["surfaces"] if item["surface"] == "gamma")
        assert gamma["payload_bytes"] == 2, body

    print("PASS")