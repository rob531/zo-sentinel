"""logic.py -- the service's data/computation layer (one concern, one file).

Imports the real application database and models. The router can remain thin
while this module performs the escalation rollup entirely through ORM queries.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_escalated_tier_rollup(db: Session) -> Dict[str, Any]:
    """Compute escalated counts and per-axis escalation rates."""
    escalated_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
            func.count(),
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
        )
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
        )
    ).all()

    by_axis: List[Dict[str, Any]] = []
    escalated_by_axis: Dict[str, int] = {}

    for axis_name, escalated_to, decision_rule_version, count in escalated_rows:
        axis_key = axis_name or "unknown"
        by_axis.append(
            {
                "axis_name": str(axis_key),
                "escalated_to": str(escalated_to or "unknown"),
                "decision_rule_version": str(decision_rule_version or "unknown"),
                "count": int(count),
            }
        )
        escalated_by_axis[axis_key] = escalated_by_axis.get(axis_key, 0) + int(count)

    total_rows = db.execute(
        select(McpLlmAxisScore.axis_name, func.count())
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    escalation_rate: List[Dict[str, Any]] = []
    for axis_name, count in total_rows:
        axis_key = axis_name or "unknown"
        total = int(count)
        escalated = escalated_by_axis.get(axis_key, 0)
        escalation_rate.append(
            {
                "axis_name": str(axis_key),
                "escalated": escalated,
                "total": total,
                "rate": float(escalated / total) if total else 0.0,
            }
        )

    return {
        "total_escalated": sum(int(row[3]) for row in escalated_rows),
        "by_axis": by_axis,
        "escalation_rate": escalation_rate,
    }


def compute_escalated_tier_rollup(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers using the compute_* convention."""
    return get_escalated_tier_rollup(db)


def _main() -> None:
    import uuid
    from typing import Optional

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    try:
        from sqlalchemy.dialects.postgresql import JSONB, UUID
        from sqlalchemy.ext.compiler import compiles

        @compiles(JSONB, "sqlite")
        def _compile_jsonb_sqlite(
            type_: Any, compiler: Any, **kwargs: Any
        ) -> str:
            return "JSON"

        @compiles(UUID, "sqlite")
        def _compile_uuid_sqlite(
            type_: Any, compiler: Any, **kwargs: Any
        ) -> str:
            return "CHAR(32)"
    except ImportError:
        pass

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    session_factory = sessionmaker = Session

    class ByAxis(BaseModel):
        axis_name: str
        escalated_to: str
        decision_rule_version: str
        count: int

    class EscalationRate(BaseModel):
        axis_name: str
        escalated: int
        total: int
        rate: float

    class RollupResponse(BaseModel):
        total_escalated: int
        by_axis: list[ByAxis]
        escalation_rate: list[EscalationRate]

    local_app = FastAPI()

    @local_app.get(
        "/api/scores/escalated-tier-rollup",
        response_model=RollupResponse,
    )
    def rollup(db: Session = Depends(get_session)) -> Dict[str, Any]:
        return get_escalated_tier_rollup(db)

    def override_get_session():
        with session_factory(bind=engine) as db:
            yield db

    local_app.dependency_overrides[get_session] = override_get_session

    id_column = McpLlmAxisScore.__table__.c.id
    id_python_type = id_column.type.python_type
    ids: list[Any] = []
    for index in range(1, 4):
        if issubclass(id_python_type, int):
            ids.append(index)
        elif issubclass(id_python_type, uuid.UUID):
            ids.append(uuid.uuid4())
        else:
            ids.append(f"score-{index}")

    with sessionmaker(bind=engine) as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=ids[0],
                    server_id="server-alpha",
                    axis_name="security",
                    label="security",
                    label_index=0,
                    p_top=0.2,
                    p_critical=0.1,
                    p_danger=0.3,
                    escalated=True,
                    escalated_to="tier-2",
                    decision_rule_version="v1",
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=ids[1],
                    server_id="server-beta",
                    axis_name="security",
                    label="security",
                    label_index=0,
                    p_top=0.1,
                    p_critical=0.0,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=ids[2],
                    server_id="server-gamma",
                    axis_name="reliability",
                    label="reliability",
                    label_index=1,
                    p_top=0.3,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=True,
                    escalated_to="tier-3",
                    decision_rule_version="v2",
                    model_version="test-model",
                ),
            ]
        )
        db.commit()

    with TestClient(local_app) as client:
        response = client.get("/api/scores/escalated-tier-rollup")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_escalated"] == 2
        assert any(
            row["axis_name"] == "security"
            and row["escalated_to"] == "tier-2"
            and row["decision_rule_version"] == "v1"
            and row["count"] == 1
            for row in payload["by_axis"]
        )
        security_rate = next(
            row
            for row in payload["escalation_rate"]
            if row["axis_name"] == "security"
        )
        assert 0.0 <= security_rate["rate"] <= 1.0
        assert security_rate["rate"] == 0.5

    print("PASS")


if __name__ == "__main__":
    _main()