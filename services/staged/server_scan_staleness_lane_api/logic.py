from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

LANE_NAMES = (
    "never_scanned",
    "older_7d",
    "older_30d",
    "older_90d",
    "fresh",
)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported last_scanned value: {type(value).__name__}")

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _age_in_days(value: Any, now: datetime) -> Optional[float]:
    scanned_at = _as_utc(value)
    if scanned_at is None:
        return None
    return max(0.0, (now - scanned_at).total_seconds() / 86400.0)


def _lane_for_age(age_days: Optional[float]) -> str:
    if age_days is None:
        return "never_scanned"
    if age_days > 90:
        return "older_90d"
    if age_days > 30:
        return "older_30d"
    if age_days > 7:
        return "older_7d"
    return "fresh"


def _median(values: Iterable[float]) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return round(float(ordered[midpoint]), 2)
    return round((ordered[midpoint - 1] + ordered[midpoint]) / 2.0, 2)


def get_scan_staleness_lanes(session: Session, days: int = 30) -> Dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    rows = session.query(McpServerRegistry).all()
    now = datetime.now(timezone.utc)

    lanes = {name: 0 for name in LANE_NAMES}
    sources: Dict[str, Dict[str, Any]] = {}

    for server in rows:
        source = server.registry_source or "unknown"
        age_days = _age_in_days(server.last_scanned, now)
        lane = _lane_for_age(age_days)
        lanes[lane] += 1

        group = sources.setdefault(
            source,
            {
                "registry_source": source,
                "total": 0,
                "never_scanned": 0,
                "stale_30d": 0,
                "stale_90d": 0,
                "_ages": [],
            },
        )
        group["total"] += 1
        if lane == "never_scanned":
            group["never_scanned"] += 1
        if age_days is not None:
            if age_days > 30:
                group["stale_30d"] += 1
            if age_days > 90:
                group["stale_90d"] += 1
            group["_ages"].append(age_days)

    by_source: List[Dict[str, Any]] = []
    for source in sorted(sources):
        group = sources[source]
        by_source.append(
            {
                "registry_source": group["registry_source"],
                "total": group["total"],
                "never_scanned": group["never_scanned"],
                "stale_30d": group["stale_30d"],
                "stale_90d": group["stale_90d"],
                "median_days_since_scan": _median(group["_ages"]),
            }
        )

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "lanes": lanes,
        "by_source": by_source,
    }


@router.get("/registry/scan-staleness-lanes")
def scan_staleness_lanes(
    days: int = Query(default=30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_scan_staleness_lanes(session=session, days=days)


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="fresh-npm",
                name="Fresh npm server",
                registry_source="npm",
                scan_count=3,
                last_scanned=base_time - __import__("datetime").timedelta(days=2),
            ),
            McpServerRegistry(
                server_id="stale-npm",
                name="Stale npm server",
                registry_source="npm",
                scan_count=1,
                last_scanned=base_time - __import__("datetime").timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="stale-github",
                name="Stale github server",
                registry_source="github",
                scan_count=1,
                last_scanned=base_time - __import__("datetime").timedelta(days=45),
            ),
            McpServerRegistry(
                server_id="never-scanned",
                name="Never scanned server",
                registry_source="github",
                scan_count=0,
                last_scanned=None,
            ),
        ]
    )
    test_session.commit()

    def override_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/registry/scan-staleness-lanes", params={"days": 30}
    )
    payload = response.json()
    assert response.status_code == 200
    assert sum(payload["lanes"].values()) == 4
    assert payload["lanes"]["older_7d"] == 1
    assert payload["lanes"]["never_scanned"] == 1
    assert any(
        group["registry_source"] == "npm" and group["total"] == 2
        for group in payload["by_source"]
    )
    print("PASS")