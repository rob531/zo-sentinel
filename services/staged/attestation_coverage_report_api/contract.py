from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["attestations"])


class AttestationCoverageResponse(BaseModel):
    registry_total: int
    attested_servers: int
    coverage_pct: float
    stale_count: int
    stale_after_days: int
    by_age_bucket: Dict[str, int]


def _has_column(session: Session, table_name: str, column_name: str) -> bool:
    inspector = inspect(session.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _attestation_rows(session: Session) -> list[tuple[object, object]]:
    created_at_available = _has_column(session, "mcp_attestations", "created_at")
    created_at_expression = "created_at" if created_at_available else "NULL"
    statement = text(
        f"SELECT server_id, {created_at_expression} "
        "FROM mcp_attestations "
        "WHERE server_id IS NOT NULL"
    )
    return [(row[0], row[1]) for row in session.execute(statement)]


def _coverage(
    session: Session,
    stale_after_days: int,
) -> AttestationCoverageResponse:
    registry_ids = {
        str(server_id)
        for server_id in session.execute(
            select(McpServerRegistry.server_id)
            .where(McpServerRegistry.server_id.is_not(None))
            .distinct()
        ).scalars()
    }
    registry_total = len(registry_ids)

    latest_by_server: dict[str, datetime | None] = {}
    for server_id, created_at in _attestation_rows(session):
        normalized_id = str(server_id)
        if normalized_id not in registry_ids:
            continue
        timestamp = _parse_timestamp(created_at)
        previous = latest_by_server.get(normalized_id, "missing")
        if previous == "missing" or (
            timestamp is not None and (previous is None or timestamp > previous)
        ):
            latest_by_server[normalized_id] = timestamp

    now = datetime.now(timezone.utc)
    buckets = {
        "0_7d": 0,
        "8_30d": 0,
        "31_90d": 0,
        "over_90d": 0,
    }
    stale_count = 0

    for timestamp in latest_by_server.values():
        if timestamp is None:
            buckets["over_90d"] += 1
            stale_count += 1
            continue

        age_seconds = max(0.0, (now - timestamp).total_seconds())
        age_days = int(age_seconds // 86400)
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

    attested_servers = len(latest_by_server)
    coverage_pct = (
        round((attested_servers / registry_total) * 100, 2)
        if registry_total
        else 0.0
    )

    return AttestationCoverageResponse(
        registry_total=registry_total,
        attested_servers=attested_servers,
        coverage_pct=coverage_pct,
        stale_count=stale_count,
        stale_after_days=stale_after_days,
        by_age_bucket=buckets,
    )


@router.get("/attestations/coverage", response_model=AttestationCoverageResponse)
def get_attestation_coverage(
    stale_after_days: int = Query(30),
    session: Session = Depends(get_session),
) -> AttestationCoverageResponse:
    bounded_stale_after_days = min(365, max(1, stale_after_days))
    return _coverage(session, bounded_stale_after_days)


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with test_session_factory() as session:
        session.execute(
            text(
                "CREATE TABLE mcp_server_registry ("
                "server_id VARCHAR PRIMARY KEY, "
                "first_seen DATETIME, "
                "last_seen DATETIME"
                ")"
            )
        )
        session.execute(
            text(
                "CREATE TABLE mcp_attestations ("
                "id INTEGER PRIMARY KEY, "
                "server_id VARCHAR NOT NULL, "
                "created_at DATETIME"
                ")"
            )
        )
        session.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, first_seen, last_seen) VALUES "
                "(:server_id, :first_seen, :last_seen)"
            ),
            [
                {
                    "server_id": "server-1",
                    "first_seen": "2025-01-01T00:00:00+00:00",
                    "last_seen": "2025-06-01T00:00:00+00:00",
                },
                {
                    "server_id": "server-2",
                    "first_seen": "2025-02-01T00:00:00+00:00",
                    "last_seen": "2025-06-01T00:00:00+00:00",
                },
                {
                    "server_id": "server-3",
                    "first_seen": "2025-03-01T00:00:00+00:00",
                    "last_seen": "2025-06-01T00:00:00+00:00",
                },
            ],
        )
        now = datetime.now(timezone.utc)
        session.execute(
            text(
                "INSERT INTO mcp_attestations "
                "(id, server_id, created_at) VALUES "
                "(1, :server_id, :created_at)"
            ),
            {
                "server_id": "server-1",
                "created_at": (now.replace(tzinfo=None) - __import__("datetime").timedelta(days=120)).isoformat(),
            },
        )
        session.execute(
            text(
                "INSERT INTO mcp_attestations "
                "(id, server_id, created_at) VALUES "
                "(2, :server_id, :created_at)"
            ),
            {
                "server_id": "server-2",
                "created_at": (now.replace(tzinfo=None) - __import__("datetime").timedelta(days=2)).isoformat(),
            },
        )
        session.commit()

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/attestations/coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["registry_total"] == 3
        assert payload["attested_servers"] == 2
        assert abs(payload["coverage_pct"] - 66.67) <= 1
        assert set(payload["by_age_bucket"]) == {
            "0_7d",
            "8_30d",
            "31_90d",
            "over_90d",
        }

    print("PASS")


if __name__ == "__main__":
    main()