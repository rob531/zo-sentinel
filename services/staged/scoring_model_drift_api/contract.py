from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


_DAYS = 30


def _iso_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    value = str(value)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value)
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _score_rows(session: Session, days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT
            s.id,
            s.server_id,
            s.axis_name,
            s.model_version,
            s.adapter_sha256,
            s.decision_rule_version,
            s.scored_at,
            s.p_top,
            s.escalated,
            r.server_id AS registry_server_id,
            r.name,
            r.risk_tier,
            r.last_assessed
        FROM mcp_llm_axis_scores AS s
        LEFT JOIN mcp_server_registry AS r
            ON r.server_id = s.server_id
        WHERE s.scored_at >= :cutoff
        ORDER BY s.scored_at ASC, s.id ASC
        """
    )
    return [dict(row) for row in session.execute(sql, {"cutoff": cutoff}).mappings().all()]


def _model_drift(session: Session, days: int) -> dict[str, Any]:
    rows = _score_rows(session, days)
    total_rows = len(rows)
    servers = {row["server_id"] for row in rows}
    most_recent = None

    if rows:
        most_recent = max(
            rows,
            key=lambda row: (_datetime_value(row["scored_at"]), row["id"]),
        )

    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    latest_by_server: dict[Any, dict[str, Any]] = {}

    for row in rows:
        key = (
            row["model_version"],
            row["adapter_sha256"],
            row["decision_rule_version"],
        )
        group = grouped.setdefault(
            key,
            {
                "model_version": row["model_version"],
                "adapter_sha256": row["adapter_sha256"],
                "decision_rule_version": row["decision_rule_version"],
                "rows": 0,
                "server_ids": set(),
                "last_scored_at": None,
            },
        )
        group["rows"] += 1
        group["server_ids"].add(row["server_id"])

        scored_at = _datetime_value(row["scored_at"])
        if group["last_scored_at"] is None or scored_at > group["last_scored_at"]:
            group["last_scored_at"] = scored_at

        previous = latest_by_server.get(row["server_id"])
        if previous is None or (
            scored_at,
            row["id"],
        ) > (
            _datetime_value(previous["scored_at"]),
            previous["id"],
        ):
            latest_by_server[row["server_id"]] = row

    versions = []
    for group in grouped.values():
        versions.append(
            {
                "model_version": group["model_version"],
                "adapter_sha256": group["adapter_sha256"],
                "decision_rule_version": group["decision_rule_version"],
                "rows": group["rows"],
                "servers": len(group["server_ids"]),
                "last_scored_at": _iso_value(group["last_scored_at"]),
                "share": round(group["rows"] / total_rows, 4) if total_rows else 0.0,
            }
        )

    versions.sort(
        key=lambda version: (
            version["last_scored_at"] is not None,
            version["last_scored_at"] or "",
            version["model_version"] or "",
            version["adapter_sha256"] or "",
            version["decision_rule_version"] or "",
        ),
        reverse=True,
    )

    latest_model = most_recent["model_version"] if most_recent is not None else None
    stale_servers = sum(
        1
        for row in latest_by_server.values()
        if row["model_version"] != latest_model
    )
    escalated_rows = sum(1 for row in rows if bool(row["escalated"]))

    return {
        "days": days,
        "total_rows": total_rows,
        "distinct_servers": len(servers),
        "versions": versions,
        "stale_servers": stale_servers,
        "escalated_rows": escalated_rows,
    }


def build_service() -> FastAPI:
    service = FastAPI(title="Scoring Model Drift API")
    prefix = "/api/scoring"

    @service.get(prefix + "/model_drift")
    def model_drift(
        days: int = Query(_DAYS, ge=1, le=3650),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return _model_drift(session, days)

    @service.get(prefix + "/model_drift/versions")
    def model_drift_versions(
        days: int = Query(_DAYS, ge=1, le=3650),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return _model_drift(session, days)

    return service


app = build_service()


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    model_version VARCHAR,
                    adapter_sha256 VARCHAR,
                    decision_rule_version VARCHAR,
                    scored_at DATETIME,
                    p_top FLOAT,
                    escalated BOOLEAN
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, last_assessed)
                VALUES
                    ('server-a', 'Server A', 'high', CURRENT_TIMESTAMP),
                    ('server-b', 'Server B', 'medium', CURRENT_TIMESTAMP),
                    ('server-c', 'Server C', 'low', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, model_version, adapter_sha256,
                     decision_rule_version, scored_at, p_top, escalated)
                VALUES
                    (1, 'server-a', 'security', 'model-v1', 'sha-a', 'rule-v1',
                     CURRENT_TIMESTAMP, 0.9, 1),
                    (2, 'server-b', 'security', 'model-v1', 'sha-a', 'rule-v1',
                     CURRENT_TIMESTAMP, 0.8, 0),
                    (3, 'server-c', 'security', 'model-v1', 'sha-a', 'rule-v1',
                     CURRENT_TIMESTAMP, 0.7, 0),
                    (4, 'server-a', 'quality', 'model-v2', 'sha-b', 'rule-v2',
                     CURRENT_TIMESTAMP, 0.6, 0)
                """
            )
        )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = build_service()
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/scoring/model_drift", params={"days": 30})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_rows"] == 4, payload
            assert payload["distinct_servers"] == 3, payload
            assert len(payload["versions"]) == 2, payload
            assert abs(sum(item["share"] for item in payload["versions"]) - 1.0) <= 1e-6
            assert payload["stale_servers"] >= 1, payload
            assert isinstance(payload["escalated_rows"], int), payload
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()