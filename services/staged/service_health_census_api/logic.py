from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session

STATUSES = ("running", "healthy", "stale", "degraded", "unknown")
STALE_AFTER_SECONDS = 300

SERVICE_HEALTH_QUERY = text(
    """
    SELECT service, status, last_heartbeat, meta
    FROM service_health
    ORDER BY service
    """
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    return normalized if normalized in STATUSES else "unknown"


def _age_seconds(last_heartbeat: Any, computed_at: datetime) -> Optional[int]:
    heartbeat = _as_utc(last_heartbeat)
    if heartbeat is None:
        return None
    age = (computed_at - heartbeat).total_seconds()
    return max(0, int(age))


def compute_health_census(session: Any, computed_at: Optional[datetime] = None) -> Dict[str, Any]:
    now = computed_at or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    result = session.execute(SERVICE_HEALTH_QUERY)
    rows = result.mappings().all()

    by_status = {status: 0 for status in STATUSES}
    stale: List[Dict[str, Any]] = []

    for row in rows:
        status = _status(row["status"])
        by_status[status] += 1

        age_seconds = _age_seconds(row["last_heartbeat"], now)
        if age_seconds is not None and age_seconds > STALE_AFTER_SECONDS:
            stale.append(
                {
                    "service": row["service"],
                    "status": status,
                    "age_seconds": age_seconds,
                    "meta": row["meta"],
                }
            )

    return {
        "total": len(rows),
        "by_status": by_status,
        "stale": stale,
        "computed_at": now.isoformat(),
    }


def get_health_census(session: Any = Depends(get_session)) -> Dict[str, Any]:
    return compute_health_census(session)


def get_service_health_census(session: Any = Depends(get_session)) -> Dict[str, Any]:
    return compute_health_census(session)


def get_census(session: Any = Depends(get_session)) -> Dict[str, Any]:
    return compute_health_census(session)


def _self_test() -> None:
    now = _utc_now()
    seeded_rows = [
        {
            "service": "sentinel-alpha",
            "status": "running",
            "last_heartbeat": now - timedelta(seconds=10),
            "meta": {"region": "local"},
        },
        {
            "service": "sentinel-beta",
            "status": "running",
            "last_heartbeat": now - timedelta(seconds=20),
            "meta": {"region": "local"},
        },
        {
            "service": "sentinel-old",
            "status": "healthy",
            "last_heartbeat": now - timedelta(seconds=600),
            "meta": {"region": "local"},
        },
        {
            "service": "sentinel-degraded",
            "status": "degraded",
            "last_heartbeat": now - timedelta(seconds=30),
            "meta": {"region": "local"},
        },
    ]

    class InMemoryResult:
        def mappings(self) -> "InMemoryResult":
            return self

        def all(self) -> List[Dict[str, Any]]:
            return seeded_rows

    class InMemorySession:
        def execute(self, statement: Any) -> InMemoryResult:
            query = str(statement)
            assert "FROM service_health" in query
            return InMemoryResult()

    class StaleEntry(BaseModel):
        service: str
        status: str
        age_seconds: int
        meta: Any = None

    class CensusResponse(BaseModel):
        total: int
        by_status: Dict[str, int]
        stale: List[StaleEntry]
        computed_at: datetime

    session = InMemorySession()
    that_app = FastAPI()

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/health/census", response_model=CensusResponse)
    def census(session: Any = Depends(get_session)) -> Dict[str, Any]:
        return get_health_census(session)

    with TestClient(that_app) as client:
        response = client.get("/api/health/census")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["by_status"]["running"] == 2
        assert payload["by_status"]["healthy"] == 1
        assert payload["by_status"]["degraded"] == 1
        assert len(payload["stale"]) == 1
        assert payload["stale"][0]["service"] == "sentinel-old"
        assert payload["stale"][0]["age_seconds"] > 300

    print("PASS")


if __name__ == "__main__":
    _self_test()