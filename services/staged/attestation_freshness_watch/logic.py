"""logic.py -- attestation freshness data/computation layer.

Reads the real mcp_attestations table and the real mcp_server_registry model.
The table is reflected so that only columns present in the deployed schema are
used.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import MetaData, String, Table, cast, inspect, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKETS = ("expired", "expiring_within_days", "valid", "missing_expiry")


def _table_columns(db: Session, table_name: str) -> List[str]:
    """Return deployed column names, preferring information_schema."""
    bind = db.get_bind()
    if bind is None:
        return []

    try:
        metadata = MetaData()
        information_columns = Table(
            "columns",
            metadata,
            schema="information_schema",
            autoload_with=bind,
        )
        rows = db.execute(
            select(information_columns.c.column_name).where(
                information_columns.c.table_name == table_name
            )
        ).scalars().all()
        if rows:
            return [str(column) for column in rows]
    except Exception:
        # SQLite and non-PostgreSQL dialects do not expose information_schema.
        pass

    try:
        return [
            str(column["name"])
            for column in inspect(bind).get_columns(table_name)
        ]
    except Exception:
        return []


def _attestation_table(db: Session) -> Table:
    metadata = MetaData()
    return Table("mcp_attestations", metadata, autoload_with=db.get_bind())


def _actual_column(columns: Sequence[str], wanted: str) -> Optional[str]:
    for column in columns:
        if column.lower() == wanted.lower():
            return column
    return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        # Handle both Unix seconds and Unix milliseconds.
        seconds = float(value)
        if abs(seconds) > 100_000_000_000:
            seconds /= 1000.0
        try:
            result = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _age_days(value: Any, now: datetime) -> Optional[int]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds() // 86400))


def _attestation_rows(
    db: Session, table: Table, columns: Sequence[str]
) -> Tuple[List[Any], List[str]]:
    wanted = (
        "server_id",
        "expires_at",
        "issued_at",
        "created_at",
        "status",
        "attestation_type",
        "attestation_hash",
    )
    selected = []
    selected_names = []
    for name in wanted:
        actual = _actual_column(columns, name)
        if actual is not None:
            selected.append(table.c[actual].label("attestation_" + name.lower()))
            selected_names.append(name.lower())

    if not selected:
        return [], []

    statement = select(*selected)
    server_id = _actual_column(columns, "server_id")
    if server_id is not None:
        statement = statement.outerjoin(
            McpServerRegistry,
            cast(table.c[server_id], String) == cast(McpServerRegistry.server_id, String),
        )

    return list(db.execute(statement).all()), selected_names


def _bucket_for(expiry: Optional[datetime], now: datetime, days: int) -> str:
    if expiry is None:
        return "missing_expiry"
    if expiry <= now:
        return "expired"
    if expiry <= now + __import__("datetime").timedelta(days=days):
        return "expiring_within_days"
    return "valid"


def compute_attestation_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute attestation freshness buckets and registry coverage."""
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        days = 30

    now = datetime.now(timezone.utc)
    table = _attestation_table(db)
    columns = _table_columns(db, "mcp_attestations")
    rows, selected_names = _attestation_rows(db, table, columns)

    counts = {bucket: 0 for bucket in _BUCKETS}
    attested_servers = set()
    oldest_unexpired: Optional[int] = None

    for row in rows:
        values = row._mapping
        server_value = values.get("attestation_server_id")
        if server_value is not None:
            attested_servers.add(str(server_value))

        expiry = _parse_datetime(values.get("attestation_expires_at"))
        bucket = _bucket_for(expiry, now, days)
        counts[bucket] += 1

        if expiry is not None and expiry > now:
            issued = values.get("attestation_issued_at")
            if issued is None:
                issued = values.get("attestation_created_at")
            age = _age_days(issued, now)
            if age is not None and (oldest_unexpired is None or age > oldest_unexpired):
                oldest_unexpired = age

    registry_rows = db.execute(
        select(McpServerRegistry.server_id)
    ).scalars().all()
    servers_without_attestation = sum(
        1 for server_id in registry_rows if str(server_id) not in attested_servers
    )

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in _BUCKETS
        ],
        "servers_without_attestation": int(servers_without_attestation),
        "oldest_unexpired_days": oldest_unexpired,
    }


def get_attestation_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name used by service routers and contracts."""
    return compute_attestation_freshness(db, days)


def attestation_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name used by service routers and contracts."""
    return compute_attestation_freshness(db, days)


def compute_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name used by service routers and contracts."""
    return compute_attestation_freshness(db, days)


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    attestation_table = Table(
        "mcp_attestations",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("server_id", String),
        Column("attestation_type", String),
        Column("attestation_hash", String),
        Column("status", String),
        Column("issued_at", DateTime),
        Column("expires_at", DateTime),
        Column("created_at", DateTime),
    )
    attestation_table.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Server 1"),
                McpServerRegistry(server_id="server-2", name="Server 2"),
                McpServerRegistry(server_id="server-3", name="Server 3"),
            ]
        )
        session.flush()
        now = datetime.now(timezone.utc)
        session.execute(
            attestation_table.insert().values(
                [
                    {
                        "server_id": "server-1",
                        "attestation_type": "oauth",
                        "attestation_hash": "hash-1",
                        "status": "valid",
                        "issued_at": now - timedelta(days=30),
                        "expires_at": now - timedelta(days=1),
                        "created_at": now,
                    },
                    {
                        "server_id": "server-2",
                        "attestation_type": "oauth",
                        "attestation_hash": "hash-2",
                        "status": "valid",
                        "issued_at": now - timedelta(days=1),
                        "expires_at": now + timedelta(days=10),
                        "created_at": now,
                    },
                    {
                        "server_id": "server-3",
                        "attestation_type": "oauth",
                        "attestation_hash": "hash-3",
                        "status": "valid",
                        "issued_at": now,
                        "expires_at": now + timedelta(days=100),
                        "created_at": now,
                    },
                ]
            )
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/attestations/freshness")
    def freshness_endpoint(days: int = 30, db: Session = Depends(get_session)):
        return compute_attestation_freshness(db, days)

    def override_session():
        with Session(engine) as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/attestations/freshness", params={"days": 10})
        assert response.status_code == 200
        payload = response.json()
        assert sum(item["count"] for item in payload["buckets"]) == 3
        assert payload["servers_without_attestation"] == 0

    print("PASS")


if __name__ == "__main__":
    _self_test()