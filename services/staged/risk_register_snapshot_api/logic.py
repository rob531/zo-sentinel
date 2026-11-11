from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc_iso(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, date):
        timestamp = datetime.combine(value, datetime.min.time())
    elif value is None:
        return ""
    else:
        raw = str(value).strip()
        if not raw:
            return ""
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    return timestamp.isoformat().replace("+00:00", "Z")


def _table_columns(session: Session, table_name: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(session.get_bind()).get_columns(table_name)
    }


def get_risk_register_snapshots(
    session: Session = Depends(get_session),
    server_id: str | None = None,
    limit: int = 50,
    db: Session | None = None,
) -> dict[str, Any]:
    active_session = db or session
    bounded_limit = max(1, min(int(limit), 1000))

    register_columns = _table_columns(active_session, "mcp_risk_register")
    registry_columns = _table_columns(active_session, McpServerRegistry.__tablename__)

    risk_tier_expression = (
        "rr.risk_tier"
        if "risk_tier" in register_columns
        else "sr.risk_tier"
    )
    verdict_expression = (
        "rr.verdict"
        if "verdict" in register_columns
        else "sr.verdict"
    )
    trust_score_expression = (
        "rr.trust_score" if "trust_score" in register_columns else "NULL"
    )

    selected_sql = f"""
        WITH selected AS (
            SELECT
                rr.id AS id,
                rr.server_id AS server_id,
                rr.computed_at AS computed_at,
                {risk_tier_expression} AS risk_tier,
                {verdict_expression} AS verdict,
                {trust_score_expression} AS trust_score,
                sr.name AS name
            FROM mcp_risk_register AS rr
            LEFT JOIN {McpServerRegistry.__tablename__} AS sr
                ON sr.server_id = rr.server_id
            {{where_clause}}
            ORDER BY rr.computed_at DESC, rr.id DESC
            {{limit_clause}}
        )
        SELECT
            id,
            server_id,
            computed_at,
            risk_tier,
            verdict,
            trust_score,
            name
        FROM selected
        ORDER BY computed_at ASC, id ASC
    """

    parameters: dict[str, Any] = {}
    if server_id is None:
        sql = selected_sql.format(
            where_clause="",
            limit_clause="LIMIT :limit",
        )
        parameters["limit"] = bounded_limit
    else:
        sql = selected_sql.format(
            where_clause="WHERE rr.server_id = :server_id",
            limit_clause="",
        )
        parameters["server_id"] = server_id

    rows = active_session.execute(text(sql), parameters).mappings().all()

    snapshots: list[dict[str, Any]] = []
    for row in rows:
        snapshots.append(
            {
                "server_id": str(row["server_id"]),
                "name": str(row["name"] or row["server_id"]),
                "computed_at": _as_utc_iso(row["computed_at"]),
                "risk_tier": str(row["risk_tier"] or ""),
                "verdict": str(row["verdict"] or ""),
                "trust_score": (
                    float(row["trust_score"])
                    if row["trust_score"] is not None
                    else None
                ),
            }
        )

    return {
        "server_id": server_id,
        "snapshots": snapshots,
        "count": len(snapshots),
        "as_of": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


get_risk_snapshots = get_risk_register_snapshots
get_register_snapshots = get_risk_register_snapshots
get_snapshots = get_risk_register_snapshots
list_risk_register_snapshots = get_risk_register_snapshots


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
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    computed_at TEXT,
                    risk_tier TEXT,
                    verdict TEXT,
                    trust_score REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, trust_score, verdict)
                VALUES
                    ('server-a', 'Alpha Server', 'HIGH', 41.5, 'quarantine'),
                    ('server-b', 'Beta Server', 'LOW', 92.0, 'trusted')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (id, server_id, computed_at, risk_tier, verdict, trust_score)
                VALUES
                    (1, 'server-a', '2025-01-01T10:00:00Z', 'MEDIUM', 'review', 60.0),
                    (2, 'server-b', '2025-01-02T10:00:00Z', 'LOW', 'trusted', 92.0),
                    (3, 'server-a', '2025-01-03T10:00:00Z', 'HIGH', 'quarantine', 41.5)
                """
            )
        )

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/risk/register/snapshots")
    def snapshots(
        server_id: str | None = None,
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_risk_register_snapshots(
            session=session,
            server_id=server_id,
            limit=limit,
        )

    response = TestClient(test_app).get("/api/risk/register/snapshots")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert len(payload["snapshots"]) == 3
    assert payload["server_id"] is None
    assert [
        snapshot["computed_at"] for snapshot in payload["snapshots"]
    ] == sorted(snapshot["computed_at"] for snapshot in payload["snapshots"])
    assert all(snapshot["name"] for snapshot in payload["snapshots"])
    assert datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00"))
    print("PASS")


if __name__ == "__main__":
    _self_test()