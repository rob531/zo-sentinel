"""Data and computation layer for adapter SHA-256 reuse auditing."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore

_SERVER_ID_CAP = 50
_MAX_LIMIT = 1000


def get_adapter_sha256_reuse_audit(
    db: Session,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return adapter groups reused by multiple servers and unscored-row counts."""
    normalized_limit = max(0, min(int(limit), _MAX_LIMIT))

    group_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.count().label("axis_rows"),
        )
        .where(
            and_(
                McpLlmAxisScore.adapter_sha256.is_not(None),
                func.trim(McpLlmAxisScore.adapter_sha256) != "",
            )
        )
        .group_by(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
        )
        .order_by(
            func.count(func.distinct(McpLlmAxisScore.server_id)).desc(),
            McpLlmAxisScore.adapter_sha256.asc(),
            McpLlmAxisScore.model_version.asc(),
        )
    ).all()

    server_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.server_id,
        )
        .where(
            and_(
                McpLlmAxisScore.adapter_sha256.is_not(None),
                func.trim(McpLlmAxisScore.adapter_sha256) != "",
            )
        )
        .group_by(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.server_id,
        )
        .order_by(
            McpLlmAxisScore.adapter_sha256.asc(),
            McpLlmAxisScore.model_version.asc(),
            McpLlmAxisScore.server_id.asc(),
        )
    ).all()

    server_ids_by_group: Dict[tuple[Any, Any], list[str]] = {}
    for adapter_sha256, model_version, server_id in server_rows:
        key = (adapter_sha256, model_version)
        server_ids = server_ids_by_group.setdefault(key, [])
        if len(server_ids) < _SERVER_ID_CAP:
            server_ids.append(str(server_id))

    total_groups = len(group_rows)
    shared_rows = [row for row in group_rows if int(row.server_count) > 1]
    shared_count = len(shared_rows)

    groups = [
        {
            "adapter_sha256": row.adapter_sha256,
            "model_version": row.model_version,
            "server_count": int(row.server_count),
            "server_ids": server_ids_by_group.get(
                (row.adapter_sha256, row.model_version),
                [],
            ),
            "axis_rows": int(row.axis_rows),
        }
        for row in shared_rows[:normalized_limit]
    ]

    unscored_adapter_rows = int(
        db.execute(
            select(func.count())
            .select_from(McpLlmAxisScore)
            .where(
                or_(
                    McpLlmAxisScore.adapter_sha256.is_(None),
                    func.trim(McpLlmAxisScore.adapter_sha256) == "",
                )
            )
        ).scalar_one()
    )

    as_of: Optional[datetime] = db.execute(
        select(func.max(McpLlmAxisScore.scored_at))
    ).scalar_one()

    return {
        "as_of": as_of,
        "groups": groups,
        "shared_count": shared_count,
        "unscored_adapter_rows": unscored_adapter_rows,
        "total_groups": total_groups,
    }


adapter_sha256_reuse_audit = get_adapter_sha256_reuse_audit
get_adapter_sha256_reuse = get_adapter_sha256_reuse_audit
get_adapter_reuse_audit = get_adapter_sha256_reuse_audit
get_adapter_reuse_summary = get_adapter_sha256_reuse_audit
get_adapter_reuse_report = get_adapter_sha256_reuse_audit
audit_adapter_sha256_reuse = get_adapter_sha256_reuse_audit


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    shared_adapter = "a" * 64
    unique_adapter = "b" * 64
    with TestSession() as test_db:
        test_db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    adapter_sha256=shared_adapter,
                    model_version="model-1",
                    server_id="server-a",
                    axis_name="security",
                    p_top=0.91,
                ),
                McpLlmAxisScore(
                    id=2,
                    adapter_sha256=shared_adapter,
                    model_version="model-1",
                    server_id="server-b",
                    axis_name="quality",
                    p_top=0.82,
                ),
                McpLlmAxisScore(
                    id=3,
                    adapter_sha256=unique_adapter,
                    model_version="model-2",
                    server_id="server-c",
                    axis_name="security",
                    p_top=0.73,
                ),
            ]
        )
        test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scoring/adapter-reuse")
    def adapter_reuse_endpoint(
        limit: int = 100,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_adapter_sha256_reuse_audit(db, limit)

    def override_get_session() -> Session:
        with TestSession() as test_db:
            yield test_db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/adapter-reuse", params={"limit": 100})
        assert response.status_code == 200
        payload = response.json()
        assert payload["shared_count"] == 1
        assert len(payload["groups"]) == 1
        assert payload["groups"][0]["adapter_sha256"] == shared_adapter
        assert payload["groups"][0]["server_count"] == 2
        assert payload["groups"][0]["axis_rows"] == 2
        assert all(
            group["adapter_sha256"] != unique_adapter
            for group in payload["groups"]
        )
        assert payload["total_groups"] == 2
        assert payload["unscored_adapter_rows"] == 0

    test_engine.dispose()
    print("PASS")