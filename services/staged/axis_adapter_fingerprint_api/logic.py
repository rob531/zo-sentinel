from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api", tags=["axis-adapter-fingerprints"])


def _validate_inputs(limit: int, since_days: int) -> None:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if not 0 <= since_days <= 365:
        raise ValueError("since_days must be between 0 and 365")


def _query(session: Session, limit: int, since_days: int) -> dict[str, Any]:
    _validate_inputs(limit, since_days)

    score = McpLlmAxisScore
    server = McpServerRegistry

    filters = []
    if since_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        filters.append(score.scored_at >= cutoff)

    totals_stmt = (
        select(
            func.count(score.id),
            func.count(func.distinct(score.adapter_sha256)),
            func.count(func.distinct(score.model_version)),
            func.count(func.distinct(score.decision_rule_version)),
        )
        .select_from(score)
        .join(server, score.server_id == server.server_id)
    )
    if filters:
        totals_stmt = totals_stmt.where(*filters)

    breakdown_stmt = (
        select(
            score.adapter_sha256.label("adapter_sha256"),
            score.model_version.label("model_version"),
            score.decision_rule_version.label("decision_rule_version"),
            func.count(score.id).label("row_count"),
            func.count(func.distinct(server.server_id)).label("server_count"),
            func.max(score.scored_at).label("last_scored_at"),
        )
        .select_from(score)
        .join(server, score.server_id == server.server_id)
    )
    if filters:
        breakdown_stmt = breakdown_stmt.where(*filters)

    breakdown_stmt = (
        breakdown_stmt
        .group_by(
            score.adapter_sha256,
            score.model_version,
            score.decision_rule_version,
        )
        .order_by(
            func.max(score.scored_at).desc(),
            score.adapter_sha256.asc(),
            score.model_version.asc(),
            score.decision_rule_version.asc(),
        )
        .limit(limit)
    )

    totals = session.execute(totals_stmt).one()
    rows = session.execute(breakdown_stmt).mappings().all()

    return {
        "days": since_days,
        "total_rows": int(totals[0] or 0),
        "adapters": [
            {
                "adapter_sha256": row["adapter_sha256"],
                "model_version": row["model_version"],
                "decision_rule_version": row["decision_rule_version"],
                "row_count": int(row["row_count"] or 0),
                "server_count": int(row["server_count"] or 0),
                "last_scored_at": row["last_scored_at"],
            }
            for row in rows
        ],
        "distinct_adapters": int(totals[1] or 0),
        "distinct_model_versions": int(totals[2] or 0),
        "distinct_rule_versions": int(totals[3] or 0),
    }


def fingerprint_breakdown(
    session: Session | None = None,
    limit: int = 50,
    since_days: int = 30,
) -> dict[str, Any]:
    if session is not None:
        return _query(session, limit, since_days)

    session_context = get_session()
    if hasattr(session_context, "__enter__"):
        with session_context as opened_session:
            return _query(opened_session, limit, since_days)
    if hasattr(session_context, "__next__"):
        opened_session = next(session_context)
        try:
            return _query(opened_session, limit, since_days)
        finally:
            close = getattr(session_context, "close", None)
            if close is not None:
                close()
    return _query(session_context, limit, since_days)


@router.get("/axis/adapter-fingerprints")
def adapter_fingerprints(
    limit: int = Query(50, ge=1, le=500),
    since_days: int = Query(30, ge=0, le=365),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return fingerprint_breakdown(session=session, limit=limit, since_days=since_days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    adapter_sha256 TEXT,
                    model_version TEXT,
                    decision_rule_version TEXT,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"
            ),
            [
                {"sid": "server-1", "name": "Server One"},
                {"sid": "server-2", "name": "Server Two"},
                {"sid": "server-3", "name": "Server Three"},
            ],
        )

        now = datetime.now(timezone.utc)
        seeded_rows = []
        row_id = 1
        for server_number in range(1, 4):
            adapter = "adapter-a" if server_number < 3 else "adapter-b"
            model = "model-a" if server_number < 3 else "model-b"
            for axis in ("risk", "quality"):
                seeded_rows.append(
                    {
                        "id": row_id,
                        "server_id": f"server-{server_number}",
                        "axis_name": axis,
                        "adapter_sha256": adapter,
                        "model_version": model,
                        "decision_rule_version": "rule-1",
                        "scored_at": now,
                    }
                )
                row_id += 1

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id, server_id, axis_name, adapter_sha256,
                    model_version, decision_rule_version, scored_at
                ) VALUES (
                    :id, :server_id, :axis_name, :adapter_sha256,
                    :model_version, :decision_rule_version, :scored_at
                )
                """
            ),
            seeded_rows,
        )

    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/adapter-fingerprints")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["adapters"]) == 2
        assert sum(item["row_count"] for item in payload["adapters"]) == len(seeded_rows)
        assert payload["total_rows"] == len(seeded_rows)
        assert payload["distinct_adapters"] == 2
        assert payload["distinct_model_versions"] == 2

    print("PASS")