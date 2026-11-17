from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def _session_from_dependency() -> Session:
    candidate = get_session()
    if isinstance(candidate, Session):
        return candidate
    return next(candidate)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_coverage(
    days: int,
    axis_name: str | None,
    session: Session | None = None,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    if axis_name is not None and axis_name not in AXIS_NAMES:
        raise ValueError(f"unknown axis_name: {axis_name}")

    owns_session = session is None
    if session is None:
        session = _session_from_dependency()

    cutoff = datetime.utcnow() - timedelta(days=days)
    score = McpLlmAxisScore
    server = McpServerRegistry

    filters = [score.scored_at >= cutoff]
    if axis_name is not None:
        filters.append(score.axis_name == axis_name)

    joined = score.__table__.join(
        server.__table__,
        score.server_id == server.server_id,
    )

    group_stmt = (
        select(
            score.model_version.label("model_version"),
            score.decision_rule_version.label("decision_rule_version"),
            score.adapter_sha256.label("adapter_sha256"),
            func.count().label("rows"),
            func.count(func.distinct(score.server_id)).label("servers"),
            func.avg(score.p_top).label("mean_p_top"),
            func.max(score.scored_at).label("last_scored_at"),
        )
        .select_from(joined)
        .where(*filters)
        .group_by(
            score.model_version,
            score.decision_rule_version,
            score.adapter_sha256,
        )
        .order_by(
            score.model_version,
            score.decision_rule_version,
            score.adapter_sha256,
        )
    )

    group_rows = session.execute(group_stmt).mappings().all()

    total_rows_stmt = (
        select(func.count())
        .select_from(joined)
        .where(*filters)
    )
    total_rows = int(session.execute(total_rows_stmt).scalar_one())

    total_servers_stmt = (
        select(func.count(func.distinct(score.server_id)))
        .select_from(joined)
        .where(*filters)
    )
    total_servers = int(session.execute(total_servers_stmt).scalar_one())

    model_stmt = (
        select(
            score.model_version.label("model_version"),
            func.count(func.distinct(score.server_id)).label("servers"),
        )
        .select_from(joined)
        .where(*filters)
        .group_by(score.model_version)
        .order_by(score.model_version)
    )
    model_rows = session.execute(model_stmt).mappings().all()

    groups = []
    for row in group_rows:
        mean_p_top = row["mean_p_top"]
        groups.append(
            {
                "model_version": row["model_version"],
                "decision_rule_version": row["decision_rule_version"],
                "adapter_sha256": row["adapter_sha256"],
                "rows": int(row["rows"]),
                "servers": int(row["servers"]),
                "mean_p_top": float(mean_p_top) if mean_p_top is not None else None,
                "last_scored_at": _iso(row["last_scored_at"]),
            }
        )

    model_versions = []
    for row in model_rows:
        servers = int(row["servers"])
        model_versions.append(
            {
                "model_version": row["model_version"],
                "servers": servers,
                "coverage_ratio": (
                    float(servers / total_servers) if total_servers else 0.0
                ),
            }
        )

    result = {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": total_rows,
        "total_servers": total_servers,
        "groups": groups,
        "model_versions": model_versions,
    }

    if owns_session:
        session.close()

    return result


router = APIRouter(prefix="/api")


@router.get("/scores/model-coverage")
def model_coverage(
    days: int = Query(default=30, ge=1),
    axis_name: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if axis_name is not None and axis_name not in AXIS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=(
                "axis_name must be one of: "
                + ", ".join(sorted(AXIS_NAMES))
            ),
        )
    return build_coverage(days, axis_name, session=session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
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
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER NOT NULL,
                    axis_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    decision_rule_version TEXT NOT NULL,
                    adapter_sha256 TEXT,
                    p_top REAL,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id INTEGER PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )

        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (1, 'server-one', 'high'),
                       (2, 'server-two', 'medium'),
                       (3, 'server-three', 'low')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, model_version,
                     decision_rule_version, adapter_sha256, p_top, scored_at)
                VALUES
                    (1, 1, 'overall_risk', 'model-a', 'rule-1', 'sha-a', 0.8, :t1),
                    (2, 2, 'overall_risk', 'model-a', 'rule-1', 'sha-a', 0.6, :t2),
                    (3, 3, 'overall_risk', 'model-b', 'rule-1', 'sha-b', 0.4, :t1)
                """
            ),
            {"t1": now - timedelta(days=1), "t2": now - timedelta(days=2)},
        )

    def override_session():
        with test_engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield Session(connection)
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
            finally:
                transaction.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session
    test_app.include_router(router)

    response = TestClient(test_app).get(
        "/api/scores/model-coverage",
        params={"days": 30, "axis_name": "overall_risk"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["groups"]) == 2, payload
    assert any(
        group["model_version"] == "model-a" and group["rows"] == 2
        for group in payload["groups"]
    ), payload
    model_a = next(
        item for item in payload["model_versions"] if item["model_version"] == "model-a"
    )
    assert abs(model_a["coverage_ratio"] - (2 / 3)) < 1e-9, payload
    print("PASS")