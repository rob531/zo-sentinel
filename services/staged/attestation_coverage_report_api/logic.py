from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/attestations", tags=["attestations"])


class AttestationCoverageResponse(BaseModel):
    registry_total: int
    attested_servers: int
    coverage_pct: float
    stale_count: int
    stale_after_days: int
    by_age_bucket: Dict[str, int]


def _normalize_server_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _registry_server_ids(session: Any) -> List[str]:
    try:
        values = session.execute(select(McpServerRegistry.server_id)).scalars().all()
    except SQLAlchemyError:
        return []
    return [
        server_id
        for value in values
        if (server_id := _normalize_server_id(value)) is not None
    ]


def _attestation_rows(session: Any) -> List[Tuple[Optional[str], Optional[datetime]]]:
    try:
        inspector = inspect(session.get_bind())
        if not inspector.has_table("mcp_attestations"):
            return []
        columns = {
            column["name"]
            for column in inspector.get_columns("mcp_attestations")
        }
    except SQLAlchemyError:
        return []

    if "server_id" not in columns:
        return []

    selected_columns = ["server_id"]
    timestamp_expression = "NULL"
    if "created_at" in columns:
        selected_columns.append("created_at")
        timestamp_expression = "created_at"

    query = text(
        "SELECT {server_id}, {created_at} FROM mcp_attestations"
        .format(server_id=selected_columns[0], created_at=timestamp_expression)
    )
    try:
        rows = session.execute(query).all()
    except SQLAlchemyError:
        return []

    normalized: List[Tuple[Optional[str], Optional[datetime]]] = []
    for server_id, created_at in rows:
        normalized.append(
            (
                _normalize_server_id(server_id),
                _normalize_timestamp(created_at),
            )
        )
    return normalized


def _latest_attestations(
    rows: Iterable[Tuple[Optional[str], Optional[datetime]]],
    registry_ids: set,
) -> Dict[str, Optional[datetime]]:
    candidates: Dict[str, List[Optional[datetime]]] = {}
    for server_id, created_at in rows:
        if server_id is None or server_id not in registry_ids:
            continue
        candidates.setdefault(server_id, []).append(created_at)

    latest: Dict[str, Optional[datetime]] = {}
    for server_id, timestamps in candidates.items():
        valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
        if valid_timestamps:
            latest[server_id] = max(valid_timestamps)
        else:
            latest[server_id] = None
    return latest


def get_attestation_coverage(
    session: Any,
    stale_after_days: int = 30,
) -> AttestationCoverageResponse:
    threshold_days = max(1, min(365, int(stale_after_days)))
    registry_ids = set(_registry_server_ids(session))
    registry_total = len(registry_ids)

    attestation_rows = _attestation_rows(session)
    latest_attestations = _latest_attestations(attestation_rows, registry_ids)
    attested_servers = len(latest_attestations)

    now = datetime.now(timezone.utc)
    buckets = {
        "0_7d": 0,
        "8_30d": 0,
        "31_90d": 0,
        "over_90d": 0,
    }
    stale_count = 0

    for created_at in latest_attestations.values():
        if created_at is None:
            buckets["over_90d"] += 1
            stale_count += 1
            continue

        age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
        if age_days <= 7:
            buckets["0_7d"] += 1
        elif age_days <= 30:
            buckets["8_30d"] += 1
        elif age_days <= 90:
            buckets["31_90d"] += 1
        else:
            buckets["over_90d"] += 1

        if age_days > threshold_days:
            stale_count += 1

    coverage_pct = (
        round((attested_servers / registry_total) * 100.0, 2)
        if registry_total
        else 0.0
    )

    return AttestationCoverageResponse(
        registry_total=registry_total,
        attested_servers=attested_servers,
        coverage_pct=coverage_pct,
        stale_count=stale_count,
        stale_after_days=threshold_days,
        by_age_bucket=buckets,
    )


@router.get("/coverage", response_model=AttestationCoverageResponse)
def attestation_coverage(
    stale_after_days: int = Query(30),
    session: Any = Depends(get_session),
) -> AttestationCoverageResponse:
    return get_attestation_coverage(session, stale_after_days)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE mcp_server_registry "
                "(server_id VARCHAR(255) PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE mcp_attestations "
                "(id INTEGER PRIMARY KEY, server_id VARCHAR(255), created_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id) VALUES "
                "(:first), (:second), (:third)"
            ),
            {
                "first": "server-one",
                "second": "server-two",
                "third": "server-three",
            },
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                "INSERT INTO mcp_attestations (id, server_id, created_at) VALUES "
                "(:old_id, :old_server, :old_created), "
                "(:fresh_id, :fresh_server, :fresh_created)"
            ),
            {
                "old_id": 1,
                "old_server": "server-one",
                "old_created": now - timedelta(days=60),
                "fresh_id": 2,
                "fresh_server": "server-two",
                "fresh_created": now - timedelta(days=2),
            },
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    response = TestClient(that_app).get("/api/attestations/coverage")
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