from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


COHORT_ORDER = (
    "never_scanned",
    "scan_overdue",
    "scan_stale",
    "scanned_recently",
)


def _clamp_days(days: int) -> int:
    return max(1, min(365, int(days)))


def get_scan_backlog_cohorts(session: Session, days: int = 30) -> dict[str, Any]:
    days = _clamp_days(days)

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=days)
    stale_cutoff = now - timedelta(days=90)

    last_scanned_type = McpServerRegistry.__table__.c.last_scanned.type
    if not getattr(last_scanned_type, "timezone", False):
        recent_cutoff = recent_cutoff.replace(tzinfo=None)
        stale_cutoff = stale_cutoff.replace(tzinfo=None)

    cohort_expression = case(
        (McpServerRegistry.last_scanned.is_(None), "never_scanned"),
        (McpServerRegistry.last_scanned >= recent_cutoff, "scanned_recently"),
        (McpServerRegistry.last_scanned > stale_cutoff, "scan_overdue"),
        else_="scan_stale",
    )

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
            cohort_expression.label("cohort"),
        )
        .select_from(McpServerRegistry)
    )

    rows = session.execute(statement).all()

    counts = {cohort: 0 for cohort in COHORT_ORDER}
    by_source: dict[str, dict[str, int]] = {
        cohort: {} for cohort in COHORT_ORDER
    }

    for row in rows:
        cohort = row.cohort
        if cohort not in counts:
            cohort = "scan_stale"

        source = row.registry_source
        source_key = str(source) if source is not None else "unknown"

        counts[cohort] += 1
        by_source[cohort][source_key] = by_source[cohort].get(source_key, 0) + 1

    cohorts = [
        {
            "cohort": cohort,
            "count": counts[cohort],
            "by_source": dict(sorted(by_source[cohort].items())),
        }
        for cohort in COHORT_ORDER
    ]

    return {
        "days": days,
        "cohorts": cohorts,
        "total": len(rows),
        "never_scanned": counts["never_scanned"],
    }


get_scan_backlog = get_scan_backlog_cohorts
get_scan_cohorts = get_scan_backlog_cohorts
build_scan_backlog_cohorts = get_scan_backlog_cohorts


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    scan_count INTEGER NOT NULL,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    risk_tier VARCHAR,
                    registry_source VARCHAR
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        seed_rows = [
            {
                "server_id": "server-null",
                "name": "Never scanned",
                "scan_count": 0,
                "last_scanned": None,
                "last_seen": now,
                "risk_tier": "medium",
                "registry_source": "seed",
            },
            {
                "server_id": "server-recent",
                "name": "Recently scanned",
                "scan_count": 1,
                "last_scanned": now - timedelta(days=3),
                "last_seen": now,
                "risk_tier": "low",
                "registry_source": "seed",
            },
            {
                "server_id": "server-overdue",
                "name": "Overdue scan",
                "scan_count": 1,
                "last_scanned": now - timedelta(days=60),
                "last_seen": now,
                "risk_tier": "high",
                "registry_source": "manual",
            },
            {
                "server_id": "server-stale",
                "name": "Stale scan",
                "scan_count": 1,
                "last_scanned": now - timedelta(days=400),
                "last_seen": now,
                "risk_tier": "critical",
                "registry_source": "manual",
            },
        ]

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    scan_count,
                    last_scanned,
                    last_seen,
                    risk_tier,
                    registry_source
                ) VALUES (
                    :server_id,
                    :name,
                    :scan_count,
                    :last_scanned,
                    :last_seen,
                    :risk_tier,
                    :registry_source
                )
                """
            ),
            seed_rows,
        )

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        database_session = session_factory()
        try:
            yield database_session
        finally:
            database_session.close()

    that_app = FastAPI()

    @that_app.get("/api/registry/scan-backlog")
    def scan_backlog(days: int = 30, session: Session = Depends(get_session)):
        return get_scan_backlog_cohorts(session, days)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-backlog")
        assert response.status_code == 200
        payload = response.json()
        cohorts = {item["cohort"]: item["count"] for item in payload["cohorts"]}
        assert payload["never_scanned"] == 1
        assert cohorts["scan_overdue"] == 1
        assert cohorts["scan_stale"] == 1
        assert payload["total"] == 4

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()