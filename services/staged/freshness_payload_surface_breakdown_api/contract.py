from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


class FreshnessSurface(BaseModel):
    surface: str
    keys: list[str]
    computed_at: str
    compute_seconds: float
    age_days: float


class FreshnessSurfacesResponse(BaseModel):
    surfaces: list[FreshnessSurface]
    total: int
    generated_at: str


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported computed_at value: {type(value).__name__}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso8601(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _payload_keys(payload: Any) -> list[str]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload.keys())


def list_freshness_surfaces(
    session: Session,
    surface: str | None = None,
) -> FreshnessSurfacesResponse:
    rows = list(
        session.execute(
            select(
                FreshnessStat.surface,
                FreshnessStat.payload,
                FreshnessStat.computed_at,
                FreshnessStat.compute_seconds,
            )
        ).all()
    )

    if not rows:
        return FreshnessSurfacesResponse(
            surfaces=[],
            total=0,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    computed_times = [_as_datetime(row.computed_at) for row in rows]
    newest_computed_at = max(computed_times)

    latest_by_surface: dict[str, tuple[Any, datetime]] = {}
    for row in rows:
        computed_at = _as_datetime(row.computed_at)
        current = latest_by_surface.get(row.surface)
        if current is None or computed_at > current[1]:
            latest_by_surface[row.surface] = (row, computed_at)

    surfaces: list[FreshnessSurface] = []
    for surface_name, (row, computed_at) in sorted(latest_by_surface.items()):
        if surface is not None and surface_name != surface:
            continue
        surfaces.append(
            FreshnessSurface(
                surface=surface_name,
                keys=_payload_keys(row.payload),
                computed_at=_iso8601(row.computed_at),
                compute_seconds=float(row.compute_seconds),
                age_days=round(
                    (newest_computed_at - computed_at).total_seconds() / 86400.0,
                    6,
                ),
            )
        )

    return FreshnessSurfacesResponse(
        surfaces=surfaces,
        total=len(surfaces),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


router = APIRouter(prefix="/api")


@router.get("/freshness/surfaces", response_model=FreshnessSurfacesResponse)
def freshness_surfaces(
    surface: str | None = None,
    session: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    return list_freshness_surfaces(session=session, surface=surface)


app = FastAPI()
app.include_router(router)


def main() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    surface VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    computed_at DATETIME NOT NULL,
                    compute_seconds FLOAT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            [
                {
                    "surface": "signals",
                    "payload": json.dumps({"beta": 2, "alpha": 1}),
                    "computed_at": "2024-01-01T00:00:00+00:00",
                    "compute_seconds": 0.25,
                },
                {
                    "surface": "registry",
                    "payload": json.dumps({"uptime": 99.5, "latency": 12}),
                    "computed_at": "2024-01-03T00:00:00+00:00",
                    "compute_seconds": 1.5,
                },
            ],
        )

    test_session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["total"] == 2, body
        assert isinstance(body["surfaces"], list), body
        assert isinstance(body["generated_at"], str), body

        by_surface = {item["surface"]: item for item in body["surfaces"]}
        assert by_surface["signals"]["keys"] == ["alpha", "beta"], body
        assert isinstance(by_surface["signals"]["compute_seconds"], float), body
        assert by_surface["signals"]["age_days"] == 2.0, body
        assert by_surface["registry"]["age_days"] == 0.0, body

    test_engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()