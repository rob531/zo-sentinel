from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_critical_axis_hotlist(
    session: Session = Depends(get_session),
    threshold: float = 0.7,
    limit: int = 50,
) -> dict[str, Any]:
    threshold = float(threshold)
    limit = int(limit)

    if threshold < 0.0 or threshold > 1.0:
        raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")

    ranked_scores = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.axis_name.label("axis_name"),
            McpLlmAxisScore.label.label("axis_label"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.p_critical.label("p_critical"),
            McpLlmAxisScore.escalated.label("escalated"),
            McpLlmAxisScore.escalated_to.label("escalated_to"),
            McpLlmAxisScore.scored_at.label("scored_at"),
            func.row_number()
            .over(
                partition_by=(
                    McpLlmAxisScore.server_id,
                    McpLlmAxisScore.axis_name,
                ),
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.axis_name != "overall_risk")
        .subquery()
    )

    statement = (
        select(
            ranked_scores.c.server_id,
            ranked_scores.c.axis_name,
            ranked_scores.c.axis_label,
            ranked_scores.c.p_top,
            ranked_scores.c.p_critical,
            ranked_scores.c.escalated,
            ranked_scores.c.escalated_to,
            ranked_scores.c.scored_at,
            McpServerRegistry.name.label("registry_name"),
            McpServerRegistry.url.label("registry_url"),
            McpServerRegistry.registry_source.label("registry_source"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            McpServerRegistry.verdict.label("verdict"),
        )
        .select_from(ranked_scores)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == ranked_scores.c.server_id,
        )
        .where(ranked_scores.c.row_number == 1)
    )

    rows = session.execute(statement).mappings().all()
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        server_id = _as_text(row["server_id"])
        if not server_id:
            continue

        p_critical = _as_float(row["p_critical"])
        p_top = _as_float(row["p_top"])
        escalated = bool(row["escalated"])
        scored_at = _as_timestamp(row["scored_at"])

        axis = {
            "axis_name": _as_text(row["axis_name"]),
            "label": _as_text(row["axis_label"]),
            "p_critical": p_critical,
            "p_top": p_top,
            "escalated": escalated,
            "escalated_to": (
                None
                if row["escalated_to"] is None
                else str(row["escalated_to"])
            ),
            "scored_at": scored_at,
        }

        server = grouped.setdefault(
            server_id,
            {
                "server_id": server_id,
                "name": _as_text(row["registry_name"]),
                "url": _as_text(row["registry_url"]),
                "registry_source": _as_text(row["registry_source"]),
                "risk_tier": _as_text(row["risk_tier"]),
                "verdict": _as_text(row["verdict"]),
                "axes": [],
                "scored_at": "",
            },
        )
        server["axes"].append(axis)
        if scored_at > server["scored_at"]:
            server["scored_at"] = scored_at

    flagged_servers: list[dict[str, Any]] = []

    for server in grouped.values():
        axes = sorted(
            server["axes"],
            key=lambda axis: (
                -axis["p_critical"],
                axis["axis_name"],
                axis["label"],
            ),
        )
        trigger_axes = [
            axis
            for axis in axes
            if axis["p_critical"] >= threshold or axis["escalated"]
        ]
        if not trigger_axes:
            continue

        max_p_critical = max((axis["p_critical"] for axis in axes), default=0.0)
        escalated = any(axis["escalated"] for axis in axes)
        escalated_to = next(
            (
                axis["escalated_to"]
                for axis in trigger_axes
                if axis["escalated"] and axis["escalated_to"] is not None
            ),
            None,
        )

        flagged_servers.append(
            {
                "server_id": server["server_id"],
                "name": server["name"],
                "url": server["url"],
                "registry_source": server["registry_source"],
                "risk_tier": server["risk_tier"],
                "verdict": server["verdict"],
                "max_p_critical": max_p_critical,
                "escalated": escalated,
                "escalated_to": escalated_to,
                "trigger_axes": [
                    {
                        "axis_name": axis["axis_name"],
                        "label": axis["label"],
                        "p_critical": axis["p_critical"],
                        "p_top": axis["p_top"],
                    }
                    for axis in trigger_axes
                ],
                "scored_at": server["scored_at"],
            }
        )

    flagged_servers.sort(
        key=lambda server: (-server["max_p_critical"], server["server_id"])
    )

    return {
        "threshold": threshold,
        "total_flagged": len(flagged_servers),
        "servers": flagged_servers[:limit],
    }


def get_critical_hotlist(
    session: Session = Depends(get_session),
    threshold: float = 0.7,
    limit: int = 50,
) -> dict[str, Any]:
    return get_critical_axis_hotlist(
        session=session,
        threshold=threshold,
        limit=limit,
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
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
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    url VARCHAR,
                    registry_source VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    label VARCHAR,
                    label_index INTEGER,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, url, registry_source, risk_tier, verdict, last_assessed)
                VALUES
                    ('server-hot', 'Hot Server', 'http://hot.example', 'catalog', 'high', 'block', :now),
                    ('server-escalated', 'Escalated Server', 'http://escalated.example', 'catalog', 'high', 'review', :now),
                    ('server-clean', 'Clean Server', 'http://clean.example', 'catalog', 'low', 'allow', :now)
                """
            ),
            {"now": datetime.now(timezone.utc)},
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top, p_critical,
                     p_danger, escalated, escalated_to, decision_rule_version, scored_at)
                VALUES
                    ('server-hot', 'prompt_injection', 'Prompt Injection', 0, 0.95, 0.9, 0.05, 0, NULL, 'v1', :now),
                    ('server-escalated', 'tool_poisoning', 'Tool Poisoning', 0, 0.5, 0.4, 0.1, 1, 'security', 'v1', :now),
                    ('server-clean', 'prompt_injection', 'Prompt Injection', 0, 0.2, 0.1, 0.01, 0, NULL, 'v1', :now)
                """
            ),
            {"now": datetime.now(timezone.utc)},
        )

    session = test_session_factory()

    def override_get_session():
        yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/scores/critical-hotlist")
    def critical_hotlist(
        threshold: float = 0.7,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_critical_axis_hotlist(
            session=db,
            threshold=threshold,
            limit=limit,
        )

    with TestClient(that_app) as client:
        response = client.get(
            "/api/scores/critical-hotlist",
            params={"threshold": 0.7, "limit": 50},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_flagged"] == 2
        assert len(payload["servers"]) == 2
        assert payload["servers"][0]["server_id"] == "server-hot"
        assert any(
            axis["axis_name"] == "prompt_injection"
            for axis in payload["servers"][0]["trigger_axes"]
        )

        limited = client.get(
            "/api/scores/critical-hotlist",
            params={"threshold": 0.7, "limit": 1},
        )
        assert limited.status_code == 200
        assert limited.json()["total_flagged"] == 2
        assert len(limited.json()["servers"]) == 1

        invalid = client.get(
            "/api/scores/critical-hotlist",
            params={"threshold": 1.01, "limit": 50},
        )
        assert invalid.status_code == 400

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()