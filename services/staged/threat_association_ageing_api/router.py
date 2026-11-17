from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_threat_association_ageing

router = APIRouter(prefix="/api")


async def _query_ageing(
    session: Session,
    days: int,
    severity: str | None,
) -> dict[str, Any]:
    result = get_threat_association_ageing(session, days, severity)
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/threat-associations/ageing")
async def threat_association_ageing(
    days: int = Query(default=90, ge=1, le=365),
    severity: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return await _query_ageing(session, days, severity)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
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
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    association_type TEXT,
                    indicator TEXT,
                    severity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    observed_at TEXT,
                    payload TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        rows = [
            ("srv-1", "critical", now - timedelta(days=3)),
            ("srv-2", "high", now - timedelta(days=20)),
            ("srv-3", "medium", now - timedelta(days=60)),
            ("srv-4", "low", now - timedelta(days=120)),
        ]
        for index, (server_id, severity_value, created_at) in enumerate(rows, start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_threat_associations
                        (id, server_id, association_type, indicator, severity,
                         created_at, observed_at, payload)
                    VALUES
                        (:id, :server_id, 'indicator', :indicator, :severity,
                         :created_at, :observed_at, :payload)
                    """
                ),
                {
                    "id": index,
                    "server_id": server_id,
                    "indicator": f"indicator-{index}",
                    "severity": severity_value,
                    "created_at": created_at.isoformat(),
                    "observed_at": created_at.isoformat(),
                    "payload": "{}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                    VALUES (:server_id, :name, :risk_tier)
                    """
                ),
                {
                    "server_id": server_id,
                    "name": f"Server {index}",
                    "risk_tier": "standard",
                },
            )

    def sqlite_ageing(
        session: Session,
        days: int,
        severity: str | None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {"days": days}
        severity_clause = ""
        if severity is not None:
            severity_clause = " AND severity = :severity"
            parameters["severity"] = severity

        records = session.execute(
            text(
                f"""
                SELECT severity, created_at
                FROM mcp_threat_associations
                WHERE julianday('now') - julianday(created_at) <= :days
                {severity_clause}
                """
            ),
            parameters,
        ).mappings().all()

        band_order = ("0-7", "8-30", "31-90", "91+")
        band_counts = {band: 0 for band in band_order}
        severity_counts: dict[str, dict[str, int]] = {}

        for record in records:
            age = max(
                0,
                (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
                ).days,
            )
            if age <= 7:
                band = "0-7"
            elif age <= 30:
                band = "8-30"
            elif age <= 90:
                band = "31-90"
            else:
                band = "91+"
            band_counts[band] += 1

            severity_value = record["severity"]
            counts = severity_counts.setdefault(
                severity_value,
                {"count": 0, "stale_count": 0},
            )
            counts["count"] += 1
            if age > days:
                counts["stale_count"] += 1

        return {
            "days": days,
            "bands": [
                {"band": band, "count": band_counts[band]}
                for band in band_order
            ],
            "by_severity": [
                {
                    "severity": severity_value,
                    "count": counts["count"],
                    "stale_count": counts["stale_count"],
                }
                for severity_value, counts in sorted(severity_counts.items())
            ],
            "total": len(records),
        }

    import services.staged.threat_association_ageing_api.router as module

    original_logic = module.get_threat_association_ageing
    module.get_threat_association_ageing = sqlite_ageing

    try:
        app = FastAPI()
        app.include_router(module.router)
        app.dependency_overrides[get_session] = lambda: TestSession()

        with TestClient(app) as client:
            response = client.get(
                "/api/threat-associations/ageing",
                params={"days": 365},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 4
            assert any(
                band["band"] == "0-7" and band["count"] == 1
                for band in payload["bands"]
            )

            filtered = client.get(
                "/api/threat-associations/ageing",
                params={"days": 30},
            )
            assert filtered.status_code == 200
            assert filtered.json()["total"] == 2

            severity_filtered = client.get(
                "/api/threat-associations/ageing",
                params={"days": 365, "severity": "critical"},
            )
            assert severity_filtered.status_code == 200
            assert severity_filtered.json()["total"] == 1
    finally:
        module.get_threat_association_ageing = original_logic

    print("PASS")