from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_REGISTRY_SCAN_QUERY = text(
    """
    SELECT
        server_id,
        registry_source,
        last_scanned,
        scan_count
    FROM mcp_server_registry
    ORDER BY registry_source, server_id
    """
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        parsed = datetime.fromisoformat(raw)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scan_count(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _source_sort_key(source: Any) -> tuple[bool, str]:
    if source is None:
        return (True, "")
    return (False, str(source))


def _compute_source_health(
    rows: Iterable[Mapping[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[Any, list[Mapping[str, Any]]] = {}

    for row in rows:
        grouped.setdefault(row["registry_source"], []).append(row)

    sources: list[dict[str, Any]] = []
    totals = {
        "server_count": 0,
        "never_scanned": 0,
        "stale_over_30d": 0,
    }
    stale_cutoff = now - timedelta(days=30)

    for registry_source in sorted(grouped, key=_source_sort_key):
        source_rows = grouped[registry_source]
        never_scanned = 0
        stale_over_30d = 0
        scan_counts: list[int] = []
        days_since_scan: list[int] = []
        stale_servers: list[dict[str, Any]] = []

        for row in source_rows:
            scan_counts.append(_scan_count(row["scan_count"]))
            scanned_at = _normalize_datetime(row["last_scanned"])

            if scanned_at is None:
                never_scanned += 1
                continue

            elapsed = now - scanned_at
            days = max(0, int(elapsed.total_seconds() // 86400))
            days_since_scan.append(days)

            if scanned_at < stale_cutoff:
                stale_over_30d += 1
                stale_servers.append(
                    {
                        "server_id": row["server_id"],
                        "days_since_last_scan": days,
                    }
                )

        stale_servers.sort(
            key=lambda item: (-item["days_since_last_scan"], str(item["server_id"]))
        )

        source_health = {
            "registry_source": registry_source,
            "server_count": len(source_rows),
            "never_scanned": never_scanned,
            "stale_over_30d": stale_over_30d,
            "median_scan_count": float(median(scan_counts)) if scan_counts else 0.0,
            "mean_scan_count": (
                sum(scan_counts) / len(scan_counts) if scan_counts else 0.0
            ),
            "median_days_since_scan": (
                float(median(days_since_scan)) if days_since_scan else 0.0
            ),
            "top_stale": stale_servers[:25],
        }
        sources.append(source_health)

        totals["server_count"] += len(source_rows)
        totals["never_scanned"] += never_scanned
        totals["stale_over_30d"] += stale_over_30d

    return sources, totals


def get_scan_cadence_health(
    db: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = db.execute(_REGISTRY_SCAN_QUERY).mappings().all()
    current_time = _normalize_datetime(now) if now is not None else _utc_now()
    sources, totals = _compute_source_health(rows, current_time)
    return {"sources": sources, "totals": totals}


def get_registry_scan_cadence_health(
    db: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_scan_cadence_health(db, now=now)


def get_scan_cadence_health_data(
    db: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_scan_cadence_health(db, now=now)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    seeded = [
        McpServerRegistry(
            server_id="alpha-1",
            name="Alpha One",
            registry_source="alpha",
            trust_score=0.91,
            verdict="trusted",
            risk_tier="low",
            first_seen=now - timedelta(days=180),
            last_seen=now - timedelta(hours=2),
            last_assessed=now - timedelta(hours=1),
            last_scanned=now - timedelta(days=5),
            scan_count=12,
        ),
        McpServerRegistry(
            server_id="alpha-2",
            name="Alpha Two",
            registry_source="alpha",
            trust_score=0.73,
            verdict="review",
            risk_tier="medium",
            first_seen=now - timedelta(days=120),
            last_seen=now - timedelta(hours=4),
            last_assessed=now - timedelta(hours=3),
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="beta-1",
            name="Beta One",
            registry_source="beta",
            trust_score=0.84,
            verdict="trusted",
            risk_tier="low",
            first_seen=now - timedelta(days=240),
            last_seen=now - timedelta(hours=6),
            last_assessed=now - timedelta(hours=2),
            last_scanned=now - timedelta(days=40),
            scan_count=4,
        ),
        McpServerRegistry(
            server_id="beta-2",
            name="Beta Two",
            registry_source="beta",
            trust_score=0.62,
            verdict="review",
            risk_tier="high",
            first_seen=now - timedelta(days=300),
            last_seen=now - timedelta(days=1),
            last_assessed=now - timedelta(hours=5),
            last_scanned=now - timedelta(days=80),
            scan_count=7,
        ),
    ]
    testing_session.add_all(seeded)
    testing_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/scan-cadence-health")
    def scan_cadence_health_endpoint(db: Session = Depends(get_session)):
        return get_scan_cadence_health(db)

    that_app.dependency_overrides[get_session] = lambda: testing_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-cadence-health")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 2
    assert sum(source["never_scanned"] for source in payload["sources"]) == 1
    assert all(
        isinstance(source["median_scan_count"], (int, float))
        and not isinstance(source["median_scan_count"], bool)
        for source in payload["sources"]
    )
    assert payload["totals"]["server_count"] == 4

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()