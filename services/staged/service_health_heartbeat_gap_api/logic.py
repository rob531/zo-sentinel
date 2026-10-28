from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


_HEALTH_SQL = text(
    """
    SELECT service, status, last_heartbeat, meta
    FROM service_health
    WHERE :table_scope = 'service_health'
    """
)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            timestamp = float(raw)
        except ValueError:
            pass
        else:
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None

        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_meta(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def get_heartbeat_gaps(
    db: Session = Depends(get_session),
    threshold_s: int = 300,
) -> dict:
    threshold = int(threshold_s)
    now = datetime.now(timezone.utc)
    rows = db.execute(_HEALTH_SQL, {"table_scope": "service_health"}).mappings().all()

    services = []
    for row in rows:
        last_heartbeat = _parse_timestamp(row.get("last_heartbeat"))
        if last_heartbeat is None:
            age_s = -1.0
            stale = True
        else:
            age_s = (now - last_heartbeat).total_seconds()
            stale = age_s > threshold

        status = row.get("status")
        service = row.get("service")
        services.append(
            {
                "service": "" if service is None else str(service),
                "status": "" if status is None else str(status),
                "age_s": float(age_s),
                "stale": bool(stale),
                "meta": _coerce_meta(row.get("meta")),
            }
        )

    services.sort(key=lambda item: item["age_s"], reverse=True)

    return {
        "as_of": now.isoformat(),
        "threshold_s": threshold,
        "total": len(services),
        "stale_count": sum(1 for item in services if item["stale"]),
        "services": services,
    }


get_health_heartbeat_gaps = get_heartbeat_gaps


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT,
                    status TEXT,
                    last_heartbeat DATETIME,
                    meta TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now.replace(hour=(now.hour - 2) % 24)
    if old >= now:
        old -= datetime.resolution * 24 * 365

    with testing_session() as session:
        session.execute(
            text(
                """
                INSERT INTO service_health (service, status, last_heartbeat, meta)
                VALUES (:service, :status, :last_heartbeat, :meta)
                """
            ),
            [
                {
                    "service": "fresh-service",
                    "status": "healthy",
                    "last_heartbeat": now,
                    "meta": json.dumps({"region": "local"}),
                },
                {
                    "service": "old-service",
                    "status": "degraded",
                    "last_heartbeat": old,
                    "meta": json.dumps({"region": "remote"}),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/health/heartbeat-gaps")
    def heartbeat_gaps(
        threshold_s: int = 300,
        session: Session = Depends(get_session),
    ):
        return get_heartbeat_gaps(db=session, threshold_s=threshold_s)

    with TestClient(that_app) as client:
        response = client.get("/api/health/heartbeat-gaps")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert payload["stale_count"] == 1
        assert payload["services"][0]["service"] == "old-service"
        assert payload["services"][0]["stale"] is True
        assert payload["services"][1]["service"] == "fresh-service"
        assert payload["services"][1]["stale"] is False

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")