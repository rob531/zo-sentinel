from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


STALENESS_THRESHOLD_SECONDS = 3600


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return datetime.now(timezone.utc)

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _payload_keys(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return []
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys()]
    return []


def _surface_lag_rows(session: Session) -> list[dict[str, Any]]:
    result = session.execute(
        text(
            "SELECT surface, payload, computed_at, compute_seconds "
            "FROM freshness_stats ORDER BY computed_at DESC"
        )
    )
    now = datetime.now(timezone.utc)
    surfaces: list[dict[str, Any]] = []

    for row in result.mappings():
        computed_at = _as_utc(row["computed_at"])
        age_seconds = max(0.0, (now - computed_at).total_seconds())
        surfaces.append(
            {
                "surface": row["surface"],
                "computed_at": computed_at.isoformat(),
                "compute_seconds": row["compute_seconds"],
                "age_seconds": age_seconds,
                "payload_keys": _payload_keys(row["payload"]),
            }
        )

    surfaces.sort(key=lambda item: item["age_seconds"], reverse=True)
    return surfaces


def _create_surface_lag_app() -> FastAPI:
    application = FastAPI()

    @application.get("/api/freshness/surface-lag")
    def surface_lag(session: Session = Depends(get_session)) -> dict[str, Any]:
        surfaces = _surface_lag_rows(session)
        return {
            "threshold_seconds": STALENESS_THRESHOLD_SECONDS,
            "surfaces": surfaces,
            "stale_count": sum(
                1
                for item in surfaces
                if item["age_seconds"] > STALENESS_THRESHOLD_SECONDS
            ),
            "total": len(surfaces),
        }

    return application


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    FreshnessStat.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO freshness_stats "
                "(surface, payload, computed_at, compute_seconds) "
                "VALUES (:surface, :payload, :computed_at, :compute_seconds)"
            ),
            [
                {
                    "surface": "recent",
                    "payload": json.dumps({"recent": True}),
                    "computed_at": now.replace(tzinfo=None),
                    "compute_seconds": 2,
                },
                {
                    "surface": "middle",
                    "payload": json.dumps({"middle": True}),
                    "computed_at": (now.replace(tzinfo=None) - __import__("datetime").timedelta(seconds=120)),
                    "compute_seconds": 3,
                },
                {
                    "surface": "very_old",
                    "payload": json.dumps({"very_old": True}),
                    "computed_at": (now.replace(tzinfo=None) - __import__("datetime").timedelta(seconds=7200)),
                    "compute_seconds": 4,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    application = _create_surface_lag_app()
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/freshness/surface-lag")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert body["stale_count"] == 1
    assert all("payload_keys" in item for item in body["surfaces"])
    assert body["total"] == 3
    assert body["threshold_seconds"] == STALENESS_THRESHOLD_SECONDS
    assert all(
        set(item["payload_keys"]) == {item["surface"]}
        for item in body["surfaces"]
    )

    print("PASS")


if __name__ == "__main__":
    main()