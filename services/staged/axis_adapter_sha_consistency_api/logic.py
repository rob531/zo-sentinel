from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def compute_adapter_sha_consistency(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.adapter_sha256,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.server_id,
    ).where(
        McpLlmAxisScore.scored_at >= cutoff,
        McpLlmAxisScore.axis_name.is_not(None),
    )

    axis_rows: Dict[str, list[tuple[Optional[str], Optional[str], Optional[str]]]] = (
        defaultdict(list)
    )

    for axis_name, adapter_sha256, model_version, scored_at, server_id in session.execute(
        statement
    ):
        axis_rows[axis_name].append(
            (adapter_sha256, model_version, server_id)
        )

    axes: list[Dict[str, Any]] = []
    for axis_name in sorted(axis_rows):
        adapter_counts: Counter[str] = Counter()
        server_adapters: Dict[str, set[str]] = defaultdict(set)

        for adapter_sha256, _model_version, server_id in axis_rows[axis_name]:
            if not adapter_sha256:
                continue
            adapter_counts[adapter_sha256] += 1
            if server_id:
                server_adapters[server_id].add(adapter_sha256)

        if not adapter_counts:
            continue

        dominant_adapter, dominant_count = min(
            adapter_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        total_scores = sum(adapter_counts.values())
        split_scored_servers = sum(
            1 for adapters in server_adapters.values() if len(adapters) > 1
        )

        axes.append(
            {
                "axis_name": axis_name,
                "distinct_adapters": len(adapter_counts),
                "dominant_adapter": dominant_adapter,
                "dominant_share": dominant_count / total_scores,
                "split_scored_servers": split_scored_servers,
            }
        )

    return {
        "days": days,
        "axes": axes,
        "max_distinct_adapters": max(
            (axis["distinct_adapters"] for axis in axes),
            default=0,
        ),
    }


def get_adapter_sha_consistency(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_adapter_sha_consistency(session, days)


def adapter_sha_consistency(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_adapter_sha_consistency(session, days)


def compute_axis_adapter_sha_consistency(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_adapter_sha_consistency(session, days)


def get_axis_adapter_sha_consistency(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_adapter_sha_consistency(session, days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                axis_name VARCHAR NOT NULL,
                adapter_sha256 VARCHAR NOT NULL,
                model_version VARCHAR NOT NULL,
                scored_at DATETIME NOT NULL,
                server_id VARCHAR NOT NULL
            )
            """
        )

    LocalSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc)
    rows = [
        ("axis-a", "adapter-a", "model-1", now, "server-1"),
        ("axis-a", "adapter-b", "model-1", now, "server-1"),
        ("axis-b", "adapter-c", "model-1", now, "server-2"),
        ("axis-b", "adapter-d", "model-1", now, "server-3"),
    ]
    with LocalSession() as local_session:
        local_session.execute(
            McpLlmAxisScore.__table__.insert(),
            [
                {
                    "axis_name": axis_name,
                    "adapter_sha256": adapter_sha256,
                    "model_version": model_version,
                    "scored_at": scored_at,
                    "server_id": server_id,
                }
                for axis_name, adapter_sha256, model_version, scored_at, server_id in rows
            ],
        )
        local_session.commit()

    def override_get_session():
        with LocalSession() as local_session:
            yield local_session

    test_app = FastAPI()

    @test_app.get("/api/axes/adapter-sha-consistency")
    def test_endpoint(days: int = 30, session: Session = Depends(get_session)):
        return compute_adapter_sha_consistency(session, days)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/adapter-sha-consistency")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["axes"]) == 2
    assert all(axis["distinct_adapters"] == 2 for axis in payload["axes"])
    assert all(axis["dominant_share"] <= 1.0 for axis in payload["axes"])
    print("PASS")