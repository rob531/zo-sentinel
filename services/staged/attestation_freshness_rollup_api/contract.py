from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as app_models
from app.db import get_session


BUCKET_LABELS = ("0-1d", "1-7d", "7-30d", "30d+")
ATTESTATION_TIMESTAMP_CANDIDATES = (
    "attested_at",
    "attestation_timestamp",
    "attestation_time",
    "last_attested_at",
    "signed_at",
    "issued_at",
    "timestamp",
    "created_at",
    "attested_on",
    "created_on",
)
_current_session: ContextVar[Optional[Session]] = ContextVar(
    "attestation_freshness_current_session", default=None
)


class FreshnessBucket(BaseModel):
    bucket: str
    count: int


class FreshnessResponse(BaseModel):
    days: int
    servers_total: int
    with_attestation: int
    missing_attestation: int
    stale_attestation: int
    oldest_attested_at: Optional[str]
    buckets: List[FreshnessBucket]


app = FastAPI(title="Attestation Freshness Rollup API")


def _model_columns(table_name: str) -> set[str]:
    metadata = getattr(app_models, "Base", None)
    metadata = getattr(metadata, "metadata", None)
    if metadata is not None and table_name in metadata.tables:
        return {column.name for column in metadata.tables[table_name].columns}

    for value in vars(app_models).values():
        if getattr(value, "__tablename__", None) == table_name:
            table = getattr(value, "__table__", None)
            if table is not None:
                return {column.name for column in table.columns}
    return set()


def _inspect_columns(session: Session, table_name: str) -> set[str]:
    try:
        return {
            column["name"]
            for column in inspect(session.get_bind()).get_columns(table_name)
        }
    except Exception:
        return _model_columns(table_name)


def _quote_identifier(session: Session, identifier: str) -> str:
    return session.get_bind().dialect.identifier_preparer.quote(identifier)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
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


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _bucket_for_age(age_seconds: float) -> str:
    age_days = age_seconds / 86400.0
    if age_days < 1.0:
        return "0-1d"
    if age_days < 7.0:
        return "1-7d"
    if age_days < 30.0:
        return "7-30d"
    return "30d+"


def _build_attestation_freshness(session: Session, days: int) -> Dict[str, Any]:
    registry_columns = _inspect_columns(session, "mcp_server_registry")
    attestation_columns = _inspect_columns(session, "mcp_attestations")

    required_registry_columns = {"server_id", "last_seen"}
    if not required_registry_columns.issubset(registry_columns):
        raise RuntimeError(
            "mcp_server_registry must contain server_id and last_seen columns"
        )

    registry_table = _quote_identifier(session, "mcp_server_registry")
    server_id_column = _quote_identifier(session, "server_id")
    last_seen_column = _quote_identifier(session, "last_seen")

    registry_rows = session.execute(
        text(
            f"SELECT {server_id_column}, {last_seen_column} "
            f"FROM {registry_table}"
        )
    ).mappings().all()

    servers: Dict[str, Dict[str, Any]] = {}
    for row in registry_rows:
        raw_server_id = row["server_id"]
        if raw_server_id is None:
            continue
        server_key = str(raw_server_id)
        last_seen = _as_datetime(row["last_seen"])
        existing = servers.get(server_key)
        if existing is None:
            servers[server_key] = {"last_seen": last_seen}
        elif last_seen is not None:
            existing_last_seen = existing.get("last_seen")
            if (
                existing_last_seen is None
                or last_seen > existing_last_seen
            ):
                existing["last_seen"] = last_seen

    timestamp_column: Optional[str] = None
    for candidate in ATTESTATION_TIMESTAMP_CANDIDATES:
        if candidate in attestation_columns:
            timestamp_column = candidate
            break

    attestations_table = _quote_identifier(session, "mcp_attestations")
    attestation_server_id_column = _quote_identifier(session, "server_id")
    if timestamp_column is None:
        attestation_sql = (
            f"SELECT {attestation_server_id_column} FROM {attestations_table}"
        )
    else:
        quoted_timestamp = _quote_identifier(session, timestamp_column)
        attestation_sql = (
            f"SELECT {attestation_server_id_column}, {quoted_timestamp} "
            f"FROM {attestations_table}"
        )

    attestation_rows = session.execute(text(attestation_sql)).mappings().all()
    attestation_times: Dict[str, List[datetime]] = {}
    for row in attestation_rows:
        raw_server_id = row["server_id"]
        if raw_server_id is None:
            continue
        server_key = str(raw_server_id)
        if timestamp_column is None:
            attestation_times.setdefault(server_key, [])
            continue
        timestamp = _as_datetime(row[timestamp_column])
        if timestamp is not None:
            attestation_times.setdefault(server_key, []).append(timestamp)

    now = datetime.now(timezone.utc)
    bucket_counts = {label: 0 for label in BUCKET_LABELS}
    with_attestation = 0
    stale_attestation = 0
    effective_times: List[datetime] = []

    for server_key, registry in servers.items():
        if server_key not in attestation_times:
            continue

        with_attestation += 1
        candidates = attestation_times[server_key]
        effective_timestamp = max(candidates) if candidates else None
        if effective_timestamp is None:
            effective_timestamp = registry.get("last_seen")

        if effective_timestamp is None:
            stale_attestation += 1
            bucket_counts["30d+"] += 1
            continue

        effective_times.append(effective_timestamp)
        age_seconds = max(0.0, (now - effective_timestamp).total_seconds())
        bucket_counts[_bucket_for_age(age_seconds)] += 1
        if age_seconds > days * 86400.0:
            stale_attestation += 1

    servers_total = len(servers)
    oldest_attested_at = _isoformat(min(effective_times)) if effective_times else None

    return {
        "days": days,
        "servers_total": servers_total,
        "with_attestation": with_attestation,
        "missing_attestation": servers_total - with_attestation,
        "stale_attestation": stale_attestation,
        "oldest_attested_at": oldest_attested_at,
        "buckets": [
            {"bucket": label, "count": bucket_counts[label]}
            for label in BUCKET_LABELS
        ],
    }


def _open_real_session() -> Tuple[Session, Any]:
    dependency = get_session()
    if hasattr(dependency, "__next__"):
        session = next(dependency)
        return session, dependency
    return dependency, None


def build_attestation_freshness(days: int = 30) -> dict:
    session = _current_session.get()
    dependency = None
    owns_session = False
    if session is None:
        session, dependency = _open_real_session()
        owns_session = True

    try:
        return _build_attestation_freshness(session, days)
    finally:
        if owns_session:
            session.close()
            if dependency is not None and hasattr(dependency, "close"):
                dependency.close()


@app.get(
    "/api/attestations/freshness",
    response_model=FreshnessResponse,
)
def attestation_freshness(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    token = _current_session.set(db)
    try:
        return build_attestation_freshness(days)
    finally:
        _current_session.reset(token)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL UNIQUE,
                    last_seen TIMESTAMP,
                    risk_tier TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    created_at TIMESTAMP
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (id, server_id, last_seen, risk_tier, verdict)
                VALUES
                    (1, 'server-a', :now, 'high', 'malicious'),
                    (2, 'server-b', :two_days_ago, 'medium', 'suspicious'),
                    (3, 'server-c', :ten_days_ago, 'low', 'trusted')
                """
            ),
            {
                "now": now,
                "two_days_ago": now.replace(microsecond=0)
                - __import__("datetime").timedelta(days=2),
                "ten_days_ago": now.replace(microsecond=0)
                - __import__("datetime").timedelta(days=10),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations (id, server_id, created_at)
                VALUES
                    (1, 'server-a', :now),
                    (2, 'server-b', :two_days_ago)
                """
            ),
            {
                "now": now,
                "two_days_ago": now.replace(microsecond=0)
                - __import__("datetime").timedelta(days=2),
            },
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/attestations/freshness")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["servers_total"] == 3, payload
            assert payload["with_attestation"] == 2, payload
            assert payload["missing_attestation"] == 1, payload
            assert len(payload["buckets"]) == 4, payload
            assert sum(item["count"] for item in payload["buckets"]) == payload[
                "with_attestation"
            ], payload
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()