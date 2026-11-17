from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session


STALE_AFTER_SECONDS = 300
MAX_STALE_SERVICE_NAMES = 20


def _parse_heartbeat(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_health_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    bind = session.get_bind()
    has_meta_column = False
    try:
        columns = inspect(bind).get_columns("service_health")
        has_meta_column = any(column.get("name") == "meta" for column in columns)
    except Exception:
        has_meta_column = False

    meta_expression = (
        "CASE WHEN meta IS NOT NULL THEN 1 ELSE 0 END"
        if has_meta_column
        else "0"
    )
    rows = session.execute(
        text(
            f"""
            SELECT
                service,
                status,
                last_heartbeat,
                {meta_expression} AS meta_present
            FROM service_health
            """
        )
    ).mappings().all()

    by_status: dict[str, int] = {}
    stale_service_names: list[str] = []
    meta_covered_rows = 0
    cutoff = datetime.now(timezone.utc).timestamp() - STALE_AFTER_SECONDS

    for row in rows:
        status = row.get("status")
        status_key = "unknown" if status is None else str(status)
        by_status[status_key] = by_status.get(status_key, 0) + 1

        if row.get("meta_present"):
            meta_covered_rows += 1

        heartbeat = _parse_heartbeat(row.get("last_heartbeat"))
        if heartbeat is None or heartbeat.timestamp() >= cutoff:
            continue

        service = row.get("service")
        if service is not None:
            stale_service_names.append(str(service))

    stale_service_names = list(dict.fromkeys(stale_service_names))[
        :MAX_STALE_SERVICE_NAMES
    ]
    total_services = len(rows)
    meta_coverage_pct = (
        round((meta_covered_rows / total_services) * 100.0, 2)
        if total_services
        else 0.0
    )

    return {
        "total_services": int(total_services),
        "by_status": {
            status: int(count)
            for status, count in sorted(by_status.items())
        },
        "stale_services": int(len(stale_service_names)),
        "stale_service_names": stale_service_names,
        "meta_coverage_pct": float(meta_coverage_pct),
    }


get_health_meta_summary = get_health_summary
get_service_health_meta_summary = get_health_summary
get_service_health_summary = get_health_summary


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT NOT NULL,
                    status TEXT,
                    meta TEXT,
                    last_heartbeat TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO service_health (service, status, meta, last_heartbeat)
                VALUES
                    (:service_1, :status_ok, :meta_1, :fresh_1),
                    (:service_2, :status_ok, :meta_2, :fresh_2),
                    (:service_3, :status_degraded, NULL, :degraded),
                    (:service_4, :status_ok, NULL, :old)
                """
            ),
            [
                {
                    "service_1": "alpha",
                    "service_2": "beta",
                    "service_3": "gamma",
                    "service_4": "delta",
                    "status_ok": "ok",
                    "status_degraded": "degraded",
                    "meta_1": '{"enabled": true}',
                    "meta_2": '{"enabled": false}',
                    "fresh_1": (now.timestamp() - 10),
                    "fresh_2": (now.timestamp() - 20),
                    "degraded": (now.timestamp() - 30),
                    "old": (now.timestamp() - 600),
                }
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/services/health-summary")
    def health_summary(session: Session = Depends(get_session)):
        return get_health_summary(session)

    response = TestClient(test_app).get("/api/services/health-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_services"] == 4
    assert payload["by_status"]["ok"] == 3
    assert payload["by_status"]["degraded"] == 1
    assert payload["stale_services"] == 1
    assert "delta" in payload["stale_service_names"]
    assert payload["meta_coverage_pct"] == 50.0
    print("PASS")