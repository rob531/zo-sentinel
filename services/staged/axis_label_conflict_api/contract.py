from collections import defaultdict
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisLabelConflict(BaseModel):
    axis_name: str
    distinct_labels: List[str]
    distinct_label_indexes: List[int]
    label_conflict: bool
    index_conflict: bool
    sample_server_ids: List[str]


class AxisLabelConflictResponse(BaseModel):
    conflicts: List[AxisLabelConflict]
    total: int


def detect_axis_label_conflicts(session: Session) -> List[Dict[str, Any]]:
    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
    ).all()

    by_axis: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        axis_name = row.axis_name
        if axis_name is not None:
            by_axis[str(axis_name)].append(row)

    conflicts: List[Dict[str, Any]] = []
    for axis_name in sorted(by_axis):
        axis_rows = by_axis[axis_name]
        distinct_labels = sorted(
            {str(row.label) for row in axis_rows if row.label is not None}
        )
        distinct_label_indexes = sorted(
            {
                row.label_index
                for row in axis_rows
                if row.label_index is not None
            }
        )

        indexes_by_label: Dict[str, set[int]] = defaultdict(set)
        for row in axis_rows:
            if row.label is not None and row.label_index is not None:
                indexes_by_label[str(row.label)].add(row.label_index)

        label_conflict = len(distinct_labels) > 1
        index_conflict = any(
            len(label_indexes) > 1 for label_indexes in indexes_by_label.values()
        )
        if not label_conflict and not index_conflict:
            continue

        sample_server_ids = sorted(
            {
                str(row.server_id)
                for row in axis_rows
                if row.server_id is not None
            }
        )
        conflicts.append(
            {
                "axis_name": axis_name,
                "distinct_labels": distinct_labels,
                "distinct_label_indexes": distinct_label_indexes,
                "label_conflict": label_conflict,
                "index_conflict": index_conflict,
                "sample_server_ids": sample_server_ids,
            }
        )

    return conflicts


router = APIRouter(prefix="/api/axis", tags=["axis"])


@router.get(
    "/label-conflicts",
    response_model=AxisLabelConflictResponse,
)
def list_axis_label_conflicts(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    conflicts = detect_axis_label_conflicts(session)
    return {"conflicts": conflicts, "total": len(conflicts)}


app = FastAPI(title="axis_label_conflict_api")
app.include_router(router)


def run_contract_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR,
                    label_index INTEGER,
                    p_top FLOAT,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top, scored_at)
                VALUES
                    ('clean-server', 'clean-axis', 'clean-label', 0, 0.9, CURRENT_TIMESTAMP),
                    ('conflict-server-1', 'conflict-axis', 'label-a', 0, 0.8, CURRENT_TIMESTAMP),
                    ('conflict-server-2', 'conflict-axis', 'label-b', 1, 0.7, CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/axis/label-conflicts")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 1, payload
            assert len(payload["conflicts"]) == 1, payload
            conflict = payload["conflicts"][0]
            assert conflict["axis_name"] == "conflict-axis", payload
            assert len(conflict["distinct_labels"]) == 2, payload
            assert conflict["label_conflict"] is True, payload
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


if __name__ == "__main__":
    run_contract_test()
    print("PASS")