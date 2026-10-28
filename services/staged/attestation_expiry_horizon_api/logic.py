from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SERVER_REGISTRY_TABLE = McpServerRegistry.__tablename__
ATTESTATIONS_TABLE = "mcp_attestations"
BUCKET_SIZE_DAYS = 30

ATTESTATION_COLUMNS = {
    "id",
    "server_id",
    "attestation_type",
    "issued_at",
    "expires_at",
    "status",
    "payload",
    "meta",
}

REGISTRY_COLUMNS = {
    "server_id",
    "name",
    "trust_score",
    "verdict",
    "last_assessed",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            result = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    result = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _table_columns(session: Session, table_name: str) -> set[str]:
    try:
        inspector = inspect(session.get_bind())
        return {column["name"] for column in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _quoted_table(session: Session, table_name: str) -> str:
    dialect = session.get_bind().dialect
    return dialect.identifier_preparer.quote(table_name)


def _quoted_column(session: Session, column_name: str) -> str:
    dialect = session.get_bind().dialect
    return dialect.identifier_preparer.quote(column_name)


def _decode_metadata(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _metadata_value(metadata: Any, names: tuple[str, ...]) -> Any:
    decoded = _decode_metadata(metadata)
    if not isinstance(decoded, dict):
        return None

    queue: list[dict[str, Any]] = [decoded]
    while queue:
        current = queue.pop(0)
        for name in names:
            if name in current and current[name] not in (None, ""):
                return current[name]
        for nested in current.values():
            if isinstance(nested, dict):
                queue.append(nested)
    return None


def _load_registry(session: Session) -> dict[str, dict[str, Any]]:
    columns = _table_columns(session, SERVER_REGISTRY_TABLE)
    selected = columns & REGISTRY_COLUMNS
    if not selected:
        return {}

    table = _quoted_table(session, SERVER_REGISTRY_TABLE)
    column_sql = ", ".join(
        _quoted_column(session, name)
        for name in sorted(selected)
    )
    result = session.execute(text(f"SELECT {column_sql} FROM {table}"))
    registry: dict[str, dict[str, Any]] = {}

    for row in result.mappings().all():
        server_id = row.get("server_id")
        if server_id is None:
            continue
        registry[str(server_id)] = dict(row)

    return registry


def _load_attestations(session: Session) -> list[dict[str, Any]] | None:
    columns = _table_columns(session, ATTESTATIONS_TABLE)
    if not columns:
        return None

    selected = columns & ATTESTATION_COLUMNS
    if not selected:
        return []

    table = _quoted_table(session, ATTESTATIONS_TABLE)
    column_sql = ", ".join(
        _quoted_column(session, name)
        for name in sorted(selected)
    )
    result = session.execute(text(f"SELECT {column_sql} FROM {table}"))
    return [dict(row) for row in result.mappings().all()]


def _registry_fallback_rows(registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for server_id, registry_row in registry.items():
        rows.append(
            {
                "server_id": server_id,
                "issued_at": registry_row.get("last_assessed"),
                "expires_at": registry_row.get("last_assessed"),
                "status": registry_row.get("verdict") or "unknown",
                "attestation_type": "registry_fallback",
                "trust_score": registry_row.get("trust_score"),
            }
        )
    return rows


def get_expiry_horizon(db: Session, days: int = 90) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    now = _utc_now()
    window_end = now + timedelta(days=days)
    registry = _load_registry(db)
    attestation_rows = _load_attestations(db)

    if attestation_rows is None:
        attestation_rows = _registry_fallback_rows(registry)

    eligible: list[dict[str, Any]] = []
    for row in attestation_rows:
        payload = row.get("payload")
        meta = row.get("meta")

        server_id = row.get("server_id")
        if server_id is None:
            server_id = _metadata_value(
                (payload, meta),
                ("server_id", "serverId"),
            )

        server_id = str(server_id) if server_id is not None else None
        registry_row = registry.get(server_id or "", {})

        issued_at = _as_datetime(row.get("issued_at"))
        if issued_at is None:
            issued_at = _as_datetime(
                _metadata_value((payload, meta), ("issued_at", "issuedAt"))
            )
        if issued_at is None:
            issued_at = _as_datetime(registry_row.get("last_assessed"))

        expires_at = _as_datetime(row.get("expires_at"))
        if expires_at is None:
            expires_at = _as_datetime(
                _metadata_value(
                    (payload, meta),
                    ("expires_at", "expiresAt", "expiration", "expiry"),
                )
            )
        if expires_at is None:
            expires_at = _as_datetime(registry_row.get("last_assessed"))

        status = row.get("status")
        if status in (None, ""):
            status = _metadata_value((payload, meta), ("status", "state"))
        if status in (None, ""):
            status = registry_row.get("verdict")
        if status in (None, ""):
            status = "unknown"

        if server_id is None or expires_at is None:
            continue
        if expires_at <= now or expires_at > window_end:
            continue

        server_name = registry_row.get("name") or server_id
        remaining_seconds = (expires_at - now).total_seconds()
        days_remaining = max(0, math.ceil(remaining_seconds / 86400.0))

        eligible.append(
            {
                "server_id": server_id,
                "name": server_name,
                "expires_at": expires_at,
                "status": str(status),
                "days_remaining": days_remaining,
                "attestation_type": row.get("attestation_type")
                or _metadata_value((payload, meta), ("attestation_type", "attestationType"))
                or "unknown",
                "issued_at": issued_at,
            }
        )

    bucket_seconds = BUCKET_SIZE_DAYS * 86400
    buckets_by_index: dict[int, int] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for item in eligible:
        elapsed = (item["expires_at"] - now).total_seconds()
        bucket_index = max(0, int(elapsed // bucket_seconds))
        maximum_index = max(0, math.ceil(days / BUCKET_SIZE_DAYS) - 1)
        bucket_index = min(bucket_index, maximum_index)
        buckets_by_index[bucket_index] = buckets_by_index.get(bucket_index, 0) + 1

        key = (item["server_id"], item["status"])
        grouped.setdefault(key, []).append(item)

    buckets = []
    for bucket_index in sorted(buckets_by_index):
        bucket_start = now + timedelta(days=bucket_index * BUCKET_SIZE_DAYS)
        bucket_end = min(
            bucket_start + timedelta(days=BUCKET_SIZE_DAYS),
            window_end,
        )
        buckets.append(
            {
                "bucket_start": _isoformat(bucket_start),
                "bucket_end": _isoformat(bucket_end),
                "count": buckets_by_index[bucket_index],
            }
        )

    expiring_servers = []
    for key in sorted(grouped):
        for item in sorted(
            grouped[key],
            key=lambda value: (value["expires_at"], value["server_id"], value["status"]),
        ):
            expiring_servers.append(
                {
                    "server_id": item["server_id"],
                    "name": item["name"],
                    "expires_at": _isoformat(item["expires_at"]),
                    "status": item["status"],
                    "days_remaining": item["days_remaining"],
                }
            )

    return {
        "days": days,
        "buckets": buckets,
        "expiring_servers": expiring_servers,
        "total_expiring": len(eligible),
    }


compute_expiry_horizon = get_expiry_horizon
get_attestation_expiry_horizon = get_expiry_horizon
get_expiry_horizon_data = get_expiry_horizon


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    attestation_type TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT,
                    meta TEXT
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
                    trust_score REAL,
                    verdict TEXT,
                    last_assessed TEXT
                )
                """
            )
        )

        seed_now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations
                    (id, server_id, attestation_type, issued_at, expires_at, status)
                VALUES
                    (1, 'server-a', 'tls', :issued_1, :expires_1, 'valid'),
                    (2, 'server-b', 'tls', :issued_2, :expires_2, 'valid'),
                    (3, 'server-b', 'oauth', :issued_3, :expires_3, 'renewal_due'),
                    (4, 'server-expired', 'tls', :issued_4, :expires_4, 'expired')
                """
            ),
            {
                "issued_1": (seed_now - timedelta(days=2)).isoformat(),
                "expires_1": (seed_now + timedelta(days=5)).isoformat(),
                "issued_2": (seed_now - timedelta(days=2)).isoformat(),
                "expires_2": (seed_now + timedelta(days=35)).isoformat(),
                "issued_3": (seed_now - timedelta(days=2)).isoformat(),
                "expires_3": (seed_now + timedelta(days=40)).isoformat(),
                "issued_4": (seed_now - timedelta(days=60)).isoformat(),
                "expires_4": (seed_now - timedelta(days=1)).isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, trust_score, verdict, last_assessed)
                VALUES
                    ('server-a', 'Server A', 0.95, 'trusted', :assessed_a),
                    ('server-b', 'Server B', 0.85, 'trusted', :assessed_b),
                    ('server-expired', 'Expired Server', 0.40, 'untrusted', :assessed_c)
                """
            ),
            {
                "assessed_a": (seed_now - timedelta(days=1)).isoformat(),
                "assessed_b": (seed_now - timedelta(days=1)).isoformat(),
                "assessed_c": (seed_now - timedelta(days=2)).isoformat(),
            },
        )

    test_app = FastAPI()

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/attestations/expiry-horizon")
    def expiry_horizon_endpoint(
        days: int = Query(default=90, ge=1),
        db: Session = Depends(get_session),
    ):
        return get_expiry_horizon(db=db, days=days)

    with TestClient(test_app) as client:
        response = client.get("/api/attestations/expiry-horizon?days=90")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["days"] == 90
        assert body["total_expiring"] == 3, body
        assert len(body["buckets"]) >= 2, body
        assert sum(bucket["count"] for bucket in body["buckets"]) == 3, body
        assert all(
            entry["server_id"] != "server-expired"
            for entry in body["expiring_servers"]
        ), body

    print("PASS")