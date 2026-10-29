from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisModelVersionPairingGroup(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    score_count: int
    distinct_server_count: int
    axis_names: List[str]
    last_scored_at: str


class AxisModelVersionPairingResponse(BaseModel):
    days: int
    axis_filter: Optional[str]
    generated_at: str
    groups: List[AxisModelVersionPairingGroup]


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_axis_model_version_pairing(
    days: int = 30,
    axis_name: Optional[str] = None,
    db: Session = Depends(get_session),
) -> AxisModelVersionPairingResponse:
    days = min(365, max(1, int(days)))
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    filters = [
        McpLlmAxisScore.scored_at >= cutoff,
        McpLlmAxisScore.scored_at <= now,
    ]
    if axis_name is not None:
        filters.append(McpLlmAxisScore.axis_name == axis_name)

    group_query = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "distinct_server_count"
            ),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(*filters)
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
        )
        .order_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
        )
    )

    axis_query = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.axis_name,
        )
        .where(*filters)
        .distinct()
    )

    group_rows = db.execute(group_query).all()
    axis_rows = db.execute(axis_query).all()

    axes_by_group: Dict[Any, List[str]] = {}
    for model_version, decision_rule_version, adapter_sha256, covered_axis in axis_rows:
        key = (model_version, decision_rule_version, adapter_sha256)
        axes_by_group.setdefault(key, []).append(str(covered_axis or ""))

    groups: List[AxisModelVersionPairingGroup] = []
    for row in group_rows:
        key = (row.model_version, row.decision_rule_version, row.adapter_sha256)
        last_scored_at = row.last_scored_at
        if isinstance(last_scored_at, datetime):
            last_scored_at_text = _isoformat_utc(last_scored_at)
        else:
            last_scored_at_text = str(last_scored_at or "")

        groups.append(
            AxisModelVersionPairingGroup(
                model_version=str(row.model_version or ""),
                decision_rule_version=str(row.decision_rule_version or ""),
                adapter_sha256=str(row.adapter_sha256 or ""),
                score_count=int(row.score_count or 0),
                distinct_server_count=int(row.distinct_server_count or 0),
                axis_names=sorted(set(axes_by_group.get(key, []))),
                last_scored_at=last_scored_at_text,
            )
        )

    return AxisModelVersionPairingResponse(
        days=days,
        axis_filter=axis_name,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        groups=groups,
    )


get_model_version_pairing = get_axis_model_version_pairing
get_axis_model_version_pairings = get_axis_model_version_pairing


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LocalSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id BIGINT PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    label_index INTEGER NOT NULL,
                    probs JSON NOT NULL,
                    p_top FLOAT NOT NULL,
                    p_critical FLOAT NOT NULL,
                    p_danger FLOAT NOT NULL,
                    escalated BOOLEAN NOT NULL,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR NOT NULL,
                    model_version VARCHAR NOT NULL,
                    adapter_sha256 VARCHAR NOT NULL,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        (
            1,
            "server-1",
            "alpha",
            "Alpha",
            0,
            '{"alpha": 0.8}',
            0.8,
            0.1,
            0.1,
            0,
            None,
            "rule-1",
            "model-a",
            "adapter-a",
            base_time - timedelta(hours=3),
        ),
        (
            2,
            "server-1",
            "beta",
            "Beta",
            1,
            '{"beta": 0.7}',
            0.7,
            0.2,
            0.1,
            0,
            None,
            "rule-1",
            "model-a",
            "adapter-a",
            base_time - timedelta(hours=2),
        ),
        (
            3,
            "server-2",
            "gamma",
            "Gamma",
            2,
            '{"gamma": 0.6}',
            0.6,
            0.3,
            0.1,
            0,
            None,
            "rule-1",
            "model-a",
            "adapter-a",
            base_time - timedelta(hours=1),
        ),
        (
            4,
            "server-3",
            "alpha",
            "Alpha",
            0,
            '{"alpha": 0.9}',
            0.9,
            0.05,
            0.05,
            0,
            None,
            "rule-1",
            "model-b",
            "adapter-b",
            base_time - timedelta(minutes=30),
        ),
    ]

    with LocalSession() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id, server_id, axis_name, label, label_index, probs,
                    p_top, p_critical, p_danger, escalated, escalated_to,
                    decision_rule_version, model_version, adapter_sha256, scored_at
                ) VALUES (
                    :id, :server_id, :axis_name, :label, :label_index, :probs,
                    :p_top, :p_critical, :p_danger, :escalated, :escalated_to,
                    :decision_rule_version, :model_version, :adapter_sha256, :scored_at
                )
                """
            ),
            [
                dict(zip(
                    (
                        "id",
                        "server_id",
                        "axis_name",
                        "label",
                        "label_index",
                        "probs",
                        "p_top",
                        "p_critical",
                        "p_danger",
                        "escalated",
                        "escalated_to",
                        "decision_rule_version",
                        "model_version",
                        "adapter_sha256",
                        "scored_at",
                    ),
                    row,
                ))
                for row in rows
            ],
        )
        session.commit()

    self_test_app = FastAPI()

    def override_get_session():
        with LocalSession() as session:
            yield session

    self_test_app.dependency_overrides[get_session] = override_get_session

    @self_test_app.get(
        "/self-test",
        response_model=AxisModelVersionPairingResponse,
    )
    def self_test_endpoint(
        days: int = 30,
        axis_name: Optional[str] = None,
        db: Session = Depends(get_session),
    ) -> AxisModelVersionPairingResponse:
        return get_axis_model_version_pairing(
            days=days,
            axis_name=axis_name,
            db=db,
        )

    with TestClient(self_test_app) as client:
        response = client.get("/self-test")
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["days"] == 30
    assert payload["axis_filter"] is None
    assert len(payload["groups"]) == 2

    multi_axis_group = next(
        group for group in payload["groups"] if group["model_version"] == "model-a"
    )
    assert multi_axis_group["score_count"] == 3
    assert multi_axis_group["distinct_server_count"] == 2
    assert multi_axis_group["axis_names"] == ["alpha", "beta", "gamma"]
    assert len(multi_axis_group["axis_names"]) == 3
    assert multi_axis_group["axis_names"] == sorted(multi_axis_group["axis_names"])
    assert isinstance(multi_axis_group["last_scored_at"], str)
    datetime.fromisoformat(multi_axis_group["last_scored_at"].replace("Z", "+00:00"))

    print("PASS")