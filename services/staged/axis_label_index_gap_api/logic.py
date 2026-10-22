from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisLabelIndexGap(BaseModel):
    axis_name: str
    label_counts: Dict[str, int] = Field(default_factory=dict)
    missing_label_index_count: int = 0
    servers_with_multiple_labels: int = 0


router = APIRouter()


@router.get(
    "/axes/label-index-gaps",
    response_model=List[AxisLabelIndexGap],
)
def get_axis_label_index_gaps(
    db: Session = Depends(get_session),
) -> List[AxisLabelIndexGap]:
    axis_names = [
        row[0]
        for row in db.query(McpLlmAxisScore.axis_name)
        .distinct()
        .order_by(McpLlmAxisScore.axis_name)
        .all()
    ]

    label_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.count(McpLlmAxisScore.id).label("label_count"),
        )
        .filter(McpLlmAxisScore.label.isnot(None))
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .all()
    )

    missing_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("missing_count"),
        )
        .filter(
            or_(
                McpLlmAxisScore.label_index.is_(None),
                McpLlmAxisScore.label_index < 0,
            )
        )
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )

    multiple_label_subquery = (
        db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.server_id,
        )
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.server_id)
        .having(
            func.count(func.distinct(McpLlmAxisScore.label)) > 1
        )
        .subquery()
    )
    multiple_label_rows = (
        db.query(
            multiple_label_subquery.c.axis_name,
            func.count().label("server_count"),
        )
        .group_by(multiple_label_subquery.c.axis_name)
        .all()
    )

    label_counts: Dict[str, Dict[str, int]] = {axis_name: {} for axis_name in axis_names}
    for axis_name, label, count in label_rows:
        label_counts.setdefault(axis_name, {})[str(label)] = int(count)

    missing_counts = {
        axis_name: int(count)
        for axis_name, count in missing_rows
    }
    multiple_label_counts = {
        axis_name: int(count)
        for axis_name, count in multiple_label_rows
    }

    return [
        AxisLabelIndexGap(
            axis_name=axis_name,
            label_counts=label_counts.get(axis_name, {}),
            missing_label_index_count=missing_counts.get(axis_name, 0),
            servers_with_multiple_labels=multiple_label_counts.get(axis_name, 0),
        )
        for axis_name in axis_names
    ]


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
                CREATE TABLE mcp_llm_axis_scores (
                    adapter_sha256 TEXT,
                    axis_name TEXT NOT NULL,
                    decision_rule_version TEXT,
                    escalated BOOLEAN,
                    escalated_to TEXT,
                    id INTEGER PRIMARY KEY,
                    label TEXT,
                    label_index INTEGER,
                    model_version TEXT,
                    p_critical REAL,
                    p_danger REAL,
                    p_top REAL,
                    probs TEXT,
                    scored_at DATETIME,
                    server_id TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, label, label_index, p_top)
                VALUES
                    (1, 'server-1', 'security', 'pass', 0, 0.90),
                    (2, 'server-1', 'security', 'fail', 1, 0.10),
                    (3, 'server-2', 'security', 'pass', 2, 0.80),
                    (4, 'server-3', 'quality', 'pass', NULL, 0.70)
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/label-index-gaps")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload == [
            {
                "axis_name": "quality",
                "label_counts": {"pass": 1},
                "missing_label_index_count": 1,
                "servers_with_multiple_labels": 0,
            },
            {
                "axis_name": "security",
                "label_counts": {"fail": 1, "pass": 2},
                "missing_label_index_count": 0,
                "servers_with_multiple_labels": 1,
            },
        ]

    test_engine.dispose()
    print("PASS")