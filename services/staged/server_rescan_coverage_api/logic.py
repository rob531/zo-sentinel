from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def rescan_coverage(db_session, stale_days: int = 30) -> dict:
    if db_session is None:
        raise HTTPException(status_code=400, detail="db_session is required")

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.last_seen,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
    )
    rows = db_session.execute(statement).all()

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=max(0, int(stale_days)))
    seven_day_cutoff = now - timedelta(days=7)
    thirty_day_cutoff = now - timedelta(days=30)

    total_servers = len(rows)
    never_scanned = 0
    scanned_le_7d = 0
    scanned_le_30d = 0
    stale = 0
    recently_scanned = 0
    scan_times: list[datetime] = []
    groups: dict[str, dict[str, int]] = {}

    for row in rows:
        registry_source = row.registry_source
        if registry_source is None:
            source_key = "unknown"
        else:
            source_key = str(registry_source)

        group = groups.setdefault(source_key, {"total": 0, "stale": 0})
        group["total"] += 1

        scanned_at = _utc_datetime(row.last_scanned)
        if scanned_at is None:
            never_scanned += 1
            continue

        scan_times.append(scanned_at)
        if scanned_at >= seven_day_cutoff:
            scanned_le_7d += 1
        if scanned_at >= thirty_day_cutoff:
            scanned_le_30d += 1
        if scanned_at >= stale_cutoff:
            recently_scanned += 1
        else:
            stale += 1
            group["stale"] += 1

    coverage_pct = (
        (recently_scanned / total_servers) * 100.0 if total_servers else 0.0
    )

    by_registry_source = [
        {
            "registry_source": source,
            "total": values["total"],
            "stale": values["stale"],
        }
        for source, values in sorted(groups.items())
    ]

    return {
        "stale_days": int(stale_days),
        "total_servers": total_servers,
        "never_scanned": never_scanned,
        "scanned_le_7d": scanned_le_7d,
        "scanned_le_30d": scanned_le_30d,
        "stale": stale,
        "coverage_pct": float(coverage_pct),
        "by_registry_source": by_registry_source,
        "oldest_last_scanned": _iso_datetime(min(scan_times) if scan_times else None),
        "newest_last_scanned": _iso_datetime(max(scan_times) if scan_times else None),
    }


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    Session = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with Session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="test",
                    last_scanned=None,
                    scan_count=0,
                    last_seen=now,
                    risk_tier="unknown",
                    verdict="unknown",
                ),
                McpServerRegistry(
                    server_id="scanned-two-days",
                    name="Scanned two days ago",
                    registry_source="test",
                    last_scanned=now - timedelta(days=2),
                    scan_count=1,
                    last_seen=now,
                    risk_tier="low",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="scanned-forty-days",
                    name="Scanned forty days ago",
                    registry_source="test",
                    last_scanned=now - timedelta(days=40),
                    scan_count=1,
                    last_seen=now,
                    risk_tier="high",
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="scanned-today",
                    name="Scanned today",
                    registry_source="test",
                    last_scanned=now,
                    scan_count=1,
                    last_seen=now,
                    risk_tier="low",
                    verdict="pass",
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()

        @test_app.get("/api/registry/rescan-coverage")
        def coverage(session=Depends(get_session)):
            return rescan_coverage(session)

        test_app.dependency_overrides[get_session] = lambda: session
        response = TestClient(test_app).get("/api/registry/rescan-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["never_scanned"] == 1
        assert payload["stale"] == 1
        assert 0 <= payload["coverage_pct"] <= 100
        assert sum(group["total"] for group in payload["by_registry_source"]) == 4


if __name__ == "__main__":
    _self_test()
    print("PASS")