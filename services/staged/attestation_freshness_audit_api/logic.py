from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_TIMESTAMP_CANDIDATES = (
    "issued_at",
    "created_at",
    "attested_at",
    "attestation_timestamp",
    "timestamp",
)


def _timestamp_value(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 100_000_000_000:
            seconds /= 1000.0
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        candidate = str(value).strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _attestation_timestamp_column(session: Session) -> str:
    inspector = inspect(session.get_bind())
    try:
        columns = inspector.get_columns("mcp_attestations")
    except Exception as exc:
        raise RuntimeError("mcp_attestations is not available") from exc

    available = {column["name"].lower(): column["name"] for column in columns}
    for candidate in _TIMESTAMP_CANDIDATES:
        if candidate in available:
            return available[candidate]

    raise RuntimeError("mcp_attestations has no supported timestamp column")


def compute_freshness_audit(
    session: Session,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    if stale_after_days < 0:
        raise ValueError("stale_after_days must be non-negative")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=stale_after_days)

    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).all()

    registry_servers: dict[str, dict[str, Any]] = {}
    for server_id, name, risk_tier, last_assessed in registry_rows:
        if server_id is None:
            continue
        registry_servers[str(server_id)] = {
            "name": name,
            "risk_tier": risk_tier,
            "last_assessed": last_assessed,
        }

    timestamp_column = _attestation_timestamp_column(session)
    quoted_timestamp_column = session.get_bind().dialect.identifier_preparer.quote(
        timestamp_column
    )
    statement = text(
        f"""
        SELECT
            a.server_id AS server_id,
            a.{quoted_timestamp_column} AS event_timestamp,
            r.name AS server_name,
            r.risk_tier AS risk_tier,
            r.last_assessed AS last_assessed
        FROM mcp_attestations AS a
        LEFT JOIN mcp_server_registry AS r
            ON r.server_id = a.server_id
        """
    )

    attestation_rows = session.execute(statement).all()

    buckets_by_date: dict[date, dict[str, int]] = {}
    attested_server_ids: set[str] = set()
    stale_count = 0

    for server_id, event_timestamp, _server_name, _risk_tier, _last_assessed in attestation_rows:
        parsed_timestamp = _timestamp_value(event_timestamp)
        bucket_date = (
            parsed_timestamp.date() if parsed_timestamp is not None else generated_at.date()
        )
        bucket = buckets_by_date.setdefault(
            bucket_date,
            {"total": 0, "stale": 0, "servers_missing": 0},
        )
        bucket["total"] += 1

        if parsed_timestamp is None or parsed_timestamp < cutoff:
            bucket["stale"] += 1
            stale_count += 1

        if server_id is not None:
            attested_server_ids.add(str(server_id))

    servers_missing = len(set(registry_servers) - attested_server_ids)

    if not buckets_by_date:
        buckets_by_date[generated_at.date()] = {
            "total": 0,
            "stale": 0,
            "servers_missing": servers_missing,
        }
    else:
        for bucket in buckets_by_date.values():
            bucket["servers_missing"] = servers_missing

    buckets = [
        {
            "date": bucket_date.isoformat(),
            "total": values["total"],
            "stale": values["stale"],
            "servers_missing": values["servers_missing"],
        }
        for bucket_date, values in sorted(buckets_by_date.items())
    ]

    return {
        "generated_at": generated_at.isoformat(),
        "stale_after_days": stale_after_days,
        "buckets": buckets,
        "totals": {
            "attestations": len(attestation_rows),
            "stale": stale_count,
            "servers_missing": servers_missing,
        },
    }


get_freshness_audit = compute_freshness_audit
get_freshness_data = compute_freshness_audit
get_freshness_report = compute_freshness_audit
build_freshness_audit = compute_freshness_audit
build_freshness_report = compute_freshness_audit
generate_freshness_audit = compute_freshness_audit


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    server_id TEXT NOT NULL,
                    issued_at TIMESTAMP NOT NULL,
                    model_name TEXT,
                    model_version TEXT,
                    verdict TEXT
                )
                """
            )
        )

    test_session = Session(test_engine)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="fresh-server",
                name="Fresh Server",
                risk_tier="low",
                last_assessed=datetime.now(timezone.utc),
            ),
            McpServerRegistry(
                server_id="stale-server",
                name="Stale Server",
                risk_tier="medium",
                last_assessed=datetime.now(timezone.utc) - timedelta(days=45),
            ),
            McpServerRegistry(
                server_id="missing-server",
                name="Missing Server",
                risk_tier="high",
                last_assessed=datetime.now(timezone.utc) - timedelta(days=60),
            ),
        ]
    )
    test_session.commit()

    now = datetime.now(timezone.utc)
    test_session.execute(
        text(
            """
            INSERT INTO mcp_attestations
                (server_id, issued_at, model_name, model_version, verdict)
            VALUES
                (:fresh_server, :fresh_time, 'model-a', '1.0', 'pass'),
                (:stale_server, :stale_time, 'model-b', '2.0', 'pass')
            """
        ),
        {
            "fresh_server": "fresh-server",
            "fresh_time": now - timedelta(days=2),
            "stale_server": "stale-server",
            "stale_time": now - timedelta(days=45),
        },
    )
    test_session.commit()

    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/attestations/freshness")
    def freshness_endpoint(session: Session = Depends(get_session)):
        return compute_freshness_audit(session, stale_after_days=30)

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/attestations/freshness")
    payload = response.json()

    assert response.status_code == 200
    assert payload["totals"]["attestations"] == 2
    assert payload["totals"]["stale"] == 1
    assert payload["totals"]["servers_missing"] == 1
    assert any(bucket["stale"] == 1 for bucket in payload["buckets"])

    print("PASS")