from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import __doc__ as _logic_doc

router = APIRouter(prefix="/api")


class AgeBuckets(BaseModel):
    key_0_7d: int = 0
    key_8_30d: int = 0
    key_31_90d: int = 0
    key_over_90d: int = 0


class AttestationCoverageResponse(BaseModel):
    registry_total: int
    attested_servers: int
    coverage_pct: float
    stale_count: int
    stale_after_days: int
    by_age_bucket: dict[str, int]


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _registry_server_ids(session: Session) -> set[str]:
    try:
        columns = {
            column["name"]
            for column in inspect(session.get_bind()).get_columns(
                McpServerRegistry.__tablename__
            )
        }
    except Exception:
        columns = {"server_id"}

    selected = []
    for name in ("server_id", "first_seen", "last_seen"):
        if name in columns:
            selected.append(getattr(McpServerRegistry, name))
    if not selected:
        selected = [McpServerRegistry.server_id]

    rows = session.execute(select(*selected)).all()
    return {
        str(row[0])
        for row in rows
        if row[0] is not None
    }


def _attestation_rows(session: Session) -> list[dict[str, Any]]:
    try:
        columns = {
            column["name"]
            for column in inspect(session.get_bind()).get_columns("mcp_attestations")
        }
    except Exception:
        return []

    selected = [name for name in ("id", "server_id", "created_at") if name in columns]
    if not selected:
        return []

    preparer = session.get_bind().dialect.identifier_preparer
    column_sql = ", ".join(preparer.quote(name) for name in selected)
    statement = text(f"SELECT {column_sql} FROM mcp_attestations")
    return [dict(row) for row in session.execute(statement).mappings().all()]


def _latest_attestations(
    rows: list[dict[str, Any]], registry_ids: set[str]
) -> list[tuple[str, datetime | None]]:
    latest: dict[str, datetime | None] = {}
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    for row in rows:
        server_id = row.get("server_id")
        if server_id is None:
            continue
        server_id = str(server_id)
        if registry_ids and server_id not in registry_ids:
            continue

        created_at = _utc_datetime(row.get("created_at"))
        previous = latest.get(server_id)
        if server_id not in latest or (
            created_at is not None
            and (previous is None or created_at > previous)
        ):
            latest[server_id] = created_at

    return list(latest.items())


def _coverage(
    session: Session, stale_after_days: int
) -> dict[str, Any]:
    registry_ids = _registry_server_ids(session)
    rows = _attestation_rows(session)
    attestations = _latest_attestations(rows, registry_ids)

    registry_total = len(registry_ids)
    attested_servers = len(attestations)
    coverage_pct = (
        round((attested_servers / registry_total) * 100.0, 2)
        if registry_total
        else 0.0
    )

    buckets = {
        "0_7d": 0,
        "8_30d": 0,
        "31_90d": 0,
        "over_90d": 0,
    }
    stale_count = 0
    now = datetime.now(timezone.utc)

    for _, created_at in attestations:
        if created_at is None:
            age_days = 91
        else:
            age_days = max(0, (now - created_at).days)

        if age_days <= 7:
            buckets["0_7d"] += 1
        elif age_days <= 30:
            buckets["8_30d"] += 1
        elif age_days <= 90:
            buckets["31_90d"] += 1
        else:
            buckets["over_90d"] += 1

        if age_days > stale_after_days:
            stale_count += 1

    return {
        "registry_total": registry_total,
        "attested_servers": attested_servers,
        "coverage_pct": coverage_pct,
        "stale_count": stale_count,
        "stale_after_days": stale_after_days,
        "by_age_bucket": buckets,
    }


@router.get(
    "/attestations/coverage",
    response_model=AttestationCoverageResponse,
)
def get_attestation_coverage(
    stale_after_days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bounded_stale_after_days = max(1, min(365, stale_after_days))
    return _coverage(session, bounded_stale_after_days)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, first_seen, last_seen)
                VALUES
                    ('server-a', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('server-b', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('server-c', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations (id, server_id, created_at)
                VALUES
                    (1, 'server-a', :old_created_at),
                    (2, 'server-b', :fresh_created_at)
                """
            ),
            {
                "old_created_at": datetime.now(timezone.utc) - timedelta(days=120),
                "fresh_created_at": datetime.now(timezone.utc) - timedelta(days=2),
            },
        )

    test_session_factory = sessionmaker(bind=test_engine)

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/attestations/coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["registry_total"] == 3, payload
        assert payload["attested_servers"] == 2, payload
        assert abs(payload["coverage_pct"] - 66.67) <= 1, payload
        assert set(payload["by_age_bucket"]) == {
            "0_7d",
            "8_30d",
            "31_90d",
            "over_90d",
        }, payload

    print("PASS")