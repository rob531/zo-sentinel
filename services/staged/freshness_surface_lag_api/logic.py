from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


THRESHOLD_SECONDS = 3600


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, bytes):
        try:
            parsed = datetime.fromisoformat(value.decode("utf-8").replace("Z", "+00:00"))
        except (ValueError, UnicodeDecodeError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_keys(payload: Any) -> list[str]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return []

    if isinstance(payload, memoryview):
        try:
            payload = payload.tobytes().decode("utf-8")
        except UnicodeDecodeError:
            return []

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return []

    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())
    return []


def _surface_lag_rows(
    session: Session,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    query = text(
        "SELECT surface, payload, computed_at, compute_seconds "
        "FROM freshness_stats "
        "ORDER BY computed_at DESC"
    )
    rows = session.execute(query).mappings().all()
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    surfaces: list[dict[str, Any]] = []
    for row in rows:
        computed_at = _parse_datetime(row["computed_at"])
        age_seconds = (
            max(0.0, (current_time - computed_at).total_seconds())
            if computed_at is not None
            else 0.0
        )
        surfaces.append(
            {
                "surface": row["surface"],
                "computed_at": row["computed_at"],
                "compute_seconds": row["compute_seconds"],
                "age_seconds": age_seconds,
                "payload_keys": _payload_keys(row["payload"]),
            }
        )

    surfaces.sort(key=lambda item: item["age_seconds"], reverse=True)
    return surfaces


def get_surface_lag(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    surfaces = _surface_lag_rows(session)
    return {
        "threshold_seconds": THRESHOLD_SECONDS,
        "surfaces": surfaces,
        "stale_count": sum(
            1 for surface in surfaces
            if surface["age_seconds"] > THRESHOLD_SECONDS
        ),
        "total": len(surfaces),
    }


get_freshness_surface_lag = get_surface_lag
list_surface_lag = get_surface_lag


if __name__ == "__main__":
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
    test_session = TestingSession()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            FreshnessStat(
                surface="surface-a",
                payload=json.dumps({"alpha": 1, "beta": 2}),
                computed_at=now,
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="surface-b",
                payload=json.dumps({"gamma": 3}),
                computed_at=now.replace(year=now.year - 1),
                compute_seconds=1.0,
            ),
            FreshnessStat(
                surface="surface-c",
                payload=json.dumps({"delta": 4}),
                computed_at=now.replace(year=now.year - 2),
                compute_seconds=2.0,
            ),
        ]
    )
    test_session.commit()

    self_app = FastAPI()

    @self_app.get("/api/freshness/surface-lag")
    def surface_lag_endpoint(session: Session = Depends(get_session)):
        return get_surface_lag(session)

    self_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(self_app).get("/api/freshness/surface-lag")

    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert body["stale_count"] == 2
    assert all("payload_keys" in surface for surface in body["surfaces"])
    test_session.close()
    print("PASS")