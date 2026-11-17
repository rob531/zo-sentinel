from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

_SCAN_SLO_SQL = text(
    """
    SELECT
        registry_source,
        last_scanned,
        scan_count
    FROM mcp_server_registry
    WHERE CAST(:max_age_days AS INTEGER) >= 0
    """
)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
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


def _scan_slo(session: Session, max_age_days: int) -> dict:
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")

    rows = session.execute(
        _SCAN_SLO_SQL,
        {"max_age_days": max_age_days},
    ).mappings().all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    sources: dict[str, dict[str, Any]] = {}
    total_servers = 0
    never_scanned = 0
    overdue = 0

    for row in rows:
        total_servers += 1
        source_value = row["registry_source"]
        registry_source = "" if source_value is None else str(source_value)
        aggregate = sources.setdefault(
            registry_source,
            {
                "total": 0,
                "never_scanned": 0,
                "overdue": 0,
                "scan_count_total": 0.0,
                "scan_count_values": 0,
            },
        )
        aggregate["total"] += 1

        raw_scanned = row["last_scanned"]
        scanned_at = _as_utc(raw_scanned)
        if raw_scanned is None:
            never_scanned += 1
            aggregate["never_scanned"] += 1
        elif scanned_at is not None and scanned_at < cutoff:
            overdue += 1
            aggregate["overdue"] += 1

        scan_count = row["scan_count"]
        if scan_count is not None:
            aggregate["scan_count_total"] += float(scan_count)
            aggregate["scan_count_values"] += 1

    by_source = []
    for registry_source in sorted(sources):
        aggregate = sources[registry_source]
        scan_count_values = aggregate["scan_count_values"]
        average = (
            aggregate["scan_count_total"] / scan_count_values
            if scan_count_values
            else 0.0
        )
        by_source.append(
            {
                "registry_source": registry_source,
                "total": aggregate["total"],
                "never_scanned": aggregate["never_scanned"],
                "overdue": aggregate["overdue"],
                "avg_scan_count": float(average),
            }
        )

    return {
        "max_age_days": max_age_days,
        "total_servers": total_servers,
        "never_scanned": never_scanned,
        "overdue": overdue,
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def scan_slo(max_age_days: int = 30) -> dict:
    session_context = get_session()
    session = next(session_context)
    try:
        return _scan_slo(session, max_age_days)
    finally:
        try:
            session.close()
        finally:
            close = getattr(session_context, "close", None)
            if close is not None:
                close()


@router.get("/registry/scan-slo")
def get_registry_scan_slo(
    max_age_days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    return _scan_slo(session, max_age_days)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    assert McpServerRegistry.__tablename__ == "mcp_server_registry"

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
                    server_id TEXT PRIMARY KEY,
                    registry_source TEXT,
                    last_scanned TIMESTAMP NULL,
                    last_seen TIMESTAMP NULL,
                    scan_count INTEGER NULL,
                    risk_tier TEXT NULL
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    fixture_rows = [
        {
            "server_id": "never-scanned",
            "registry_source": "source-a",
            "last_scanned": None,
            "last_seen": now,
            "scan_count": 0,
            "risk_tier": "unscored",
        },
        {
            "server_id": "recent",
            "registry_source": "source-a",
            "last_scanned": now - timedelta(days=1),
            "last_seen": now,
            "scan_count": 4,
            "risk_tier": "low",
        },
        {
            "server_id": "overdue-b",
            "registry_source": "source-b",
            "last_scanned": now - timedelta(days=40),
            "last_seen": now,
            "scan_count": 2,
            "risk_tier": "medium",
        },
        {
            "server_id": "overdue-c",
            "registry_source": "source-c",
            "last_scanned": now - timedelta(days=31),
            "last_seen": now,
            "scan_count": 1,
            "risk_tier": "high",
        },
    ]

    with engine.begin() as connection:
        for row in fixture_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id,
                        registry_source,
                        last_scanned,
                        last_seen,
                        scan_count,
                        risk_tier
                    ) VALUES (
                        :server_id,
                        :registry_source,
                        :last_scanned,
                        :last_seen,
                        :scan_count,
                        :risk_tier
                    )
                    """
                ),
                row,
            )

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/scan-slo",
            params={"max_age_days": 30},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["max_age_days"] == 30
    assert payload["total_servers"] == 4
    assert payload["never_scanned"] == 1
    assert payload["overdue"] == 2
    assert len(payload["by_source"]) == 3
    assert {entry["registry_source"] for entry in payload["by_source"]} == {
        "source-a",
        "source-b",
        "source-c",
    }
    assert all(
        isinstance(entry["avg_scan_count"], float)
        for entry in payload["by_source"]
    )
    assert datetime.fromisoformat(payload["generated_at"]).tzinfo is not None

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")