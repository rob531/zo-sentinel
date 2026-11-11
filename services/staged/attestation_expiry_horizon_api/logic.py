from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKETS = ("expired", "0-7", "8-30", "31-90", ">90")


def _as_utc_datetime(value: Any) -> datetime | None:
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
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(raw)
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


def _expiry_bucket(expires_at: Any, now: datetime) -> str:
    expiry = _as_utc_datetime(expires_at)
    if expiry is None:
        return "expired"

    days = (expiry - now).total_seconds() / 86400.0
    if days < 0:
        return "expired"
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    return ">90"


def _attestation_rows(db: Session) -> list[dict[str, Any]]:
    result = db.execute(text("SELECT * FROM mcp_attestations"))
    return [dict(row) for row in result.mappings().all()]


def _registry_server_ids(db: Session) -> set[str]:
    result = db.execute(
        text("SELECT server_id FROM mcp_server_registry")
    )
    return {
        str(row[0])
        for row in result.all()
        if row[0] is not None
    }


def get_expiry_horizon(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = _attestation_rows(db)
    registry_server_ids = _registry_server_ids(db)
    now = datetime.now(timezone.utc)

    by_bucket = {bucket: 0 for bucket in _BUCKETS}
    by_status: Counter[str] = Counter()
    orphan_servers: Counter[str] = Counter()
    orphan_count = 0

    for row in rows:
        bucket = _expiry_bucket(row.get("expires_at"), now)
        by_bucket[bucket] += 1

        status = row.get("status")
        by_status["unknown" if status is None else str(status)] += 1

        server_id = row.get("server_id")
        server_key = None if server_id is None else str(server_id)
        if server_key is None or server_key not in registry_server_ids:
            orphan_count += 1
            if server_key is not None:
                orphan_servers[server_key] += 1

    per_server_top = [
        {"server_id": server_id, "count": count}
        for server_id, count in sorted(
            orphan_servers.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "total": len(rows),
        "by_bucket": by_bucket,
        "by_status": dict(by_status),
        "orphan_count": orphan_count,
        "per_server_top": per_server_top,
    }


def get_attestation_expiry_horizon(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_expiry_horizon(db)


def get_expiry_horizon_stats(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_expiry_horizon(db)


def compute_expiry_horizon(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_expiry_horizon(db)


def get_stats(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_expiry_horizon(db)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    issued_at DATETIME,
                    expires_at DATETIME,
                    status TEXT,
                    meta TEXT
                )
                """
            )
        )

    columns = inspect(engine).get_columns(McpServerRegistry.__table__.name)
    registry_values: dict[str, Any] = {"server_id": "known-server"}
    for column in columns:
        if column["name"] == "server_id" or column.get("default") is not None:
            continue
        if not column.get("nullable", True):
            column_type = str(column.get("type", ""))
            if "INTEGER" in column_type.upper():
                registry_values[column["name"]] = 0
            elif "FLOAT" in column_type.upper() or "NUMERIC" in column_type.upper():
                registry_values[column["name"]] = 0.0
            elif "BOOLEAN" in column_type.upper():
                registry_values[column["name"]] = False
            elif "DATE" in column_type.upper() or "TIME" in column_type.upper():
                registry_values[column["name"]] = datetime.now(timezone.utc)
            else:
                registry_values[column["name"]] = "test"

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    session.execute(McpServerRegistry.__table__.insert().values(**registry_values))

    now = datetime.now(timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO mcp_attestations
                (server_id, issued_at, expires_at, status)
            VALUES
                (:known1, :issued, :expiry1, 'active'),
                (:known2, :issued, :expiry2, 'active'),
                (:known3, :issued, :expiry3, 'expired'),
                (:orphan1, :issued, :expiry4, 'active'),
                (:orphan2, :issued, :expiry5, 'active')
            """
        ),
        {
            "known1": "known-server",
            "known2": "known-server",
            "known3": "known-server",
            "orphan1": "orphan-server",
            "orphan2": "orphan-server",
            "issued": now,
            "expiry1": now + __import__("datetime").timedelta(days=-5),
            "expiry2": now + __import__("datetime").timedelta(days=3),
            "expiry3": now + __import__("datetime").timedelta(days=45),
            "expiry4": now + __import__("datetime").timedelta(days=200),
            "expiry5": now + __import__("datetime").timedelta(days=200),
        },
    )
    session.commit()

    that_app = FastAPI()

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/attestations/expiry-horizon")
    def endpoint(db: Session = Depends(get_session)):
        return get_expiry_horizon(db)

    response = TestClient(that_app).get("/api/attestations/expiry-horizon")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["by_bucket"]["expired"] == 1
    assert payload["orphan_count"] == 2
    print("PASS")


if __name__ == "__main__":
    _self_test()