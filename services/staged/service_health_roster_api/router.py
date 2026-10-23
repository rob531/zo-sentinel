"""API router for the sentinel service health roster."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.db import get_session

try:
    from .logic import build_roster
except ImportError:
    def _utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def build_roster(rows: list[dict], now: datetime, threshold: int) -> dict:
        """Build the service-health roster from read-only database rows."""
        current_time = _utc_datetime(now)
        services: list[dict[str, Any]] = []
        counts = {"ok": 0, "stale": 0, "other": 0}

        for row in rows:
            service = str(row.get("service", ""))
            status = str(row.get("status", ""))
            heartbeat = row.get("last_heartbeat")
            meta = row.get("meta")

            if not isinstance(meta, dict):
                meta = {}

            if heartbeat is None:
                age_seconds = None
                last_heartbeat = None
                stale = True
            else:
                heartbeat_utc = _utc_datetime(heartbeat)
                age_seconds = (current_time - heartbeat_utc).total_seconds()
                last_heartbeat = heartbeat_utc.isoformat()
                stale = age_seconds > threshold

            if stale:
                counts["stale"] += 1
            elif status.strip().lower() == "ok":
                counts["ok"] += 1
            else:
                counts["other"] += 1

            services.append(
                {
                    "service": service,
                    "status": status,
                    "last_heartbeat": last_heartbeat,
                    "age_seconds": age_seconds,
                    "stale": stale,
                    "meta": meta,
                }
            )

        return {
            "generated_at": current_time.isoformat(),
            "stale_threshold_seconds": threshold,
            "counts": counts,
            "services": services,
        }


router = APIRouter(prefix="/api", tags=["service-health"])


@router.get("/ops/service-health")
async def get_service_health_roster(session=Depends(get_session)) -> dict:
    """Return the live service roster and heartbeat staleness state."""
    result = await session.execute(
        text(
            """
            SELECT service, status, last_heartbeat, meta
            FROM service_health
            """
        )
    )
    rows = [dict(row) for row in result.mappings().all()]
    return build_roster(rows, datetime.now(timezone.utc), 300)


if __name__ == "__main__":
    test_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    test_rows = [
        {
            "service": "fresh",
            "status": "ok",
            "last_heartbeat": datetime(2024, 1, 1, 11, 59, 0, tzinfo=timezone.utc),
            "meta": {},
        },
        {
            "service": "stopped",
            "status": "ok",
            "last_heartbeat": datetime(2024, 1, 1, 11, 50, 0, tzinfo=timezone.utc),
            "meta": {},
        },
        {
            "service": "errored",
            "status": "error",
            "last_heartbeat": datetime(2024, 1, 1, 11, 59, 0, tzinfo=timezone.utc),
            "meta": {"reason": "test"},
        },
    ]
    test_result = build_roster(test_rows, test_now, 300)
    assert test_result["counts"] == {"ok": 1, "stale": 1, "other": 1}
    assert test_result["services"][1]["stale"] is True
    assert all(
        set(service) == {
            "service",
            "status",
            "last_heartbeat",
            "age_seconds",
            "stale",
            "meta",
        }
        for service in test_result["services"]
    )
    print("PASS")