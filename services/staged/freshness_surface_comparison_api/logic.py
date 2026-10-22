from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import FreshnessStat
except ImportError:
    from app.models import FreshnessStat as FreshnessStat


_FRESHNESS_QUERY = text(
    """
    SELECT surface, payload, computed_at, compute_seconds
    FROM (
        SELECT
            surface,
            payload,
            computed_at,
            compute_seconds,
            ROW_NUMBER() OVER (
                PARTITION BY surface
                ORDER BY (computed_at IS NULL) ASC, computed_at DESC
            ) AS row_number
        FROM freshness_stats
    ) AS ranked
    WHERE row_number = 1
    ORDER BY surface
    """
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)

    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if not isinstance(value, str):
        raise ValueError(f"Unsupported computed_at value: {value!r}")

    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"

    parsed = datetime.fromisoformat(candidate)
    return _as_utc(parsed)


def _payload_as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {"value": value}

    if isinstance(value, dict):
        return dict(value)

    return {"value": value}


def compare_freshness_surfaces(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    rows = session.execute(_FRESHNESS_QUERY).mappings().all()

    surfaces: list[dict[str, Any]] = []
    for row in rows:
        computed_at = _parse_datetime(row["computed_at"])
        age_seconds = (current_time - computed_at).total_seconds()

        compute_seconds = row["compute_seconds"]
        if compute_seconds is not None:
            compute_seconds = float(compute_seconds)

        surfaces.append(
            {
                "surface": row["surface"],
                "age_seconds": age_seconds,
                "stale": age_seconds > float(max_age_seconds),
                "compute_seconds": compute_seconds,
                "payload": _payload_as_dict(row["payload"]),
            }
        )

    return {
        "surfaces": surfaces,
        "stale_count": sum(1 for surface in surfaces if surface["stale"]),
        "total": len(surfaces),
    }


def get_freshness_surface_comparison(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    return compare_freshness_surfaces(session, max_age_seconds, now)


def get_freshness_comparison(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    return compare_freshness_surfaces(session, max_age_seconds, now)


def compare_surfaces(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    return compare_freshness_surfaces(session, max_age_seconds, now)


def get_surface_comparison(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    return compare_freshness_surfaces(session, max_age_seconds, now)


def list_surface_comparisons(
    session: Session,
    max_age_seconds: float = 86400,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return compare_freshness_surfaces(session, max_age_seconds, now)["surfaces"]


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestingSession() as test_session:
        test_session.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface TEXT NOT NULL,
                    payload TEXT,
                    computed_at DATETIME NOT NULL,
                    compute_seconds REAL
                )
                """
            )
        )

        current_time = datetime.now(timezone.utc)
        test_session.execute(
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
                    "surface": "alpha",
                    "payload": json.dumps({"source": "alpha", "healthy": True}),
                    "computed_at": current_time.replace(tzinfo=None)
                    - __import__("datetime").timedelta(seconds=200),
                    "compute_seconds": 1.5,
                },
                {
                    "surface": "alpha",
                    "payload": json.dumps({"source": "alpha", "healthy": True}),
                    "computed_at": current_time.replace(tzinfo=None)
                    - __import__("datetime").timedelta(seconds=5),
                    "compute_seconds": 0.75,
                },
                {
                    "surface": "beta",
                    "payload": json.dumps({"source": "beta", "healthy": False}),
                    "computed_at": current_time.replace(tzinfo=None)
                    - __import__("datetime").timedelta(seconds=120),
                    "compute_seconds": 2.25,
                },
            ],
        )
        test_session.commit()

        def override_get_session():
            yield test_session

        that_app = FastAPI()

        @that_app.get("/api/freshness/surfaces/compare")
        def compare_endpoint(
            max_age_seconds: int = 86400,
            session: Session = Depends(get_session),
        ):
            return compare_freshness_surfaces(session, max_age_seconds)

        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get(
                "/api/freshness/surfaces/compare",
                params={"max_age_seconds": 60},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["total"] == 2
            assert result["stale_count"] == 1
            assert len(result["surfaces"]) == 2
            assert sum(1 for item in result["surfaces"] if item["stale"]) == 1
            assert all(isinstance(item["payload"], dict) for item in result["surfaces"])
            assert all("source" in item["payload"] for item in result["surfaces"])

    print("PASS")