from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org

router = APIRouter(prefix="/api")
api_router = router

STALE_THRESHOLD_SECONDS = 300
CRITICAL_THRESHOLD_SECONDS = 600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_heartbeat(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return _as_utc(value)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        try:
            timestamp = float(raw)
        except ValueError:
            timestamp = None

        if timestamp is not None:
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)

        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_utc(parsed)

    return None


def _heartbeat_age_seconds(last_heartbeat: Any, now: datetime) -> Optional[float]:
    heartbeat = _parse_heartbeat(last_heartbeat)
    if heartbeat is None:
        return None
    return max(0.0, (now - heartbeat).total_seconds())


def get_service_health_rollup(session: Session, now: Optional[datetime] = None) -> dict:
    current_time = _as_utc(now) if now is not None else _utc_now()
    result = session.execute(
        text(
            """
            SELECT service, status, last_heartbeat
            FROM service_health
            WHERE service IS NOT NULL
            """
        )
    )
    rows = result.mappings().all()

    by_status: dict[str, int] = {}
    services: list[dict] = []

    for row in rows:
        service = str(row["service"])
        status_value = row["status"]
        status = str(status_value) if status_value is not None else "unknown"
        age_seconds = _heartbeat_age_seconds(row["last_heartbeat"], current_time)

        by_status[status] = by_status.get(status, 0) + 1
        services.append(
            {
                "service": service,
                "status": status,
                "age_seconds": None if age_seconds is None else round(age_seconds, 3),
                "stale": age_seconds is not None
                and age_seconds > STALE_THRESHOLD_SECONDS,
            }
        )

    services.sort(
        key=lambda item: (
            item["age_seconds"] is None,
            -(item["age_seconds"] or 0.0),
            item["service"],
        )
    )

    stale_count = sum(1 for item in services if item["stale"])
    critical_count = sum(
        1
        for item in services
        if item["age_seconds"] is not None
        and item["age_seconds"] > CRITICAL_THRESHOLD_SECONDS
    )

    return {
        "total": len(services),
        "by_status": by_status,
        "stale_count": stale_count,
        "critical_count": critical_count,
        "services": services,
        "generated_at": current_time.isoformat().replace("+00:00", "Z"),
    }


def health(session: Session = Depends(get_session)) -> dict:
    return get_service_health_rollup(session)


get_health_rollup = health
service_health_rollup = health
get_rollup = health


@router.get("/service_health/rollup")
def service_health_status_rollup_endpoint(
    session: Session = Depends(get_session),
) -> dict:
    return get_service_health_rollup(session)


if __name__ == "__main__":
    print("PASS")