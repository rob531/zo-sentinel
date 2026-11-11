from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import Date as SqlDate
from sqlalchemy import cast, create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


def _payload_keys(payload: Any) -> list[str]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return []
    if isinstance(payload, Mapping):
        return sorted(str(key) for key in payload.keys())
    return []


def _iso_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def create_app() -> FastAPI:
    service_app = FastAPI(title="freshness_stats_trend")

    @service_app.get("/api/freshness/trend")
    def freshness_stats_trend(
        days: int = Query(default=30, ge=1),
        session=Depends(get_session),
    ) -> dict[str, Any]:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            day_expression = cast(
                func.date_trunc("day", FreshnessStat.computed_at),
                SqlDate,
            )
        else:
            day_expression = func.date(FreshnessStat.computed_at)

        statement = (
            select(
                FreshnessStat.surface,
                day_expression.label("day"),
                FreshnessStat.compute_seconds,
                FreshnessStat.payload,
                FreshnessStat.computed_at,
            )
            .order_by(
                FreshnessStat.surface,
                cast(FreshnessStat.computed_at, SqlDate),
                FreshnessStat.computed_at,
            )
        )

        rows = session.execute(statement).all()
        today = datetime.now(timezone.utc).date()
        cutoff = today - timedelta(days=days - 1)

        grouped: dict[str, dict[str, Any]] = {}
        latest_by_surface: dict[str, Any] = {}

        for row in rows:
            surface = str(row.surface)
            day_value = row.day
            if isinstance(day_value, datetime):
                day_value = day_value.date()
            if isinstance(day_value, date):
                point_date = day_value.isoformat()
            else:
                point_date = str(day_value)[:10]

            compute_seconds = (
                float(row.compute_seconds)
                if row.compute_seconds is not None
                else 0.0
            )
            point = {
                "date": point_date,
                "compute_seconds": compute_seconds,
                "payload_keys": _payload_keys(row.payload),
            }

            surface_data = grouped.setdefault(surface, {"points": {}, "latest": None})
            surface_data["points"][point_date] = point

            previous_latest = surface_data["latest"]
            if previous_latest is None or row.computed_at > previous_latest:
                surface_data["latest"] = row.computed_at

            previous_surface_latest = latest_by_surface.get(surface)
            if (
                previous_surface_latest is None
                or row.computed_at > previous_surface_latest
            ):
                latest_by_surface[surface] = row.computed_at

        surfaces: list[dict[str, Any]] = []
        for surface in sorted(grouped):
            surface_data = grouped[surface]
            latest_at = surface_data["latest"]
            stale_days: int | None = None
            if latest_at is not None:
                comparable_latest = latest_at
                if comparable_latest.tzinfo is None:
                    comparable_latest = comparable_latest.replace(tzinfo=timezone.utc)
                stale_days = max(
                    0,
                    (today - comparable_latest.astimezone(timezone.utc).date()).days,
                )

            points = [
                surface_data["points"][point_date]
                for point_date in sorted(surface_data["points"])
                if date.fromisoformat(point_date) >= cutoff
            ]
            surfaces.append(
                {
                    "surface": surface,
                    "points": points,
                    "latest_at": _iso_timestamp(latest_at),
                    "stale_days": stale_days,
                }
            )

        return {"days": days, "surfaces": surfaces}

    return service_app


def run_contract() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    computed_at DATETIME NOT NULL,
                    compute_seconds FLOAT
                )
                """
            )
        )

        today = datetime.now(timezone.utc).date()
        rows: list[dict[str, Any]] = []
        for surface in ("alpha", "beta"):
            for offset in (2, 1, 0):
                rows.append(
                    {
                        "surface": surface,
                        "payload": json.dumps(
                            {
                                "surface": surface,
                                "count": offset + 1,
                                "healthy": True,
                            }
                        ),
                        "computed_at": f"{today - timedelta(days=offset)} 00:00:00",
                        "compute_seconds": float(offset + 1) / 10,
                    }
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
            rows,
        )

    session = testing_session()
    that_app = create_app()
    that_app.dependency_overrides[get_session] = lambda: session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/freshness/trend", params={"days": 30})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["days"] == 30
        assert len(body["surfaces"]) == 2

        surfaces = {surface["surface"]: surface for surface in body["surfaces"]}
        assert len(surfaces["alpha"]["points"]) == 3
        assert len(surfaces["beta"]["points"]) == 3
        assert isinstance(surfaces["alpha"]["stale_days"], int)
        assert surfaces["alpha"]["stale_days"] >= 0
        assert isinstance(surfaces["beta"]["stale_days"], int)
        assert surfaces["beta"]["stale_days"] >= 0
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_contract()