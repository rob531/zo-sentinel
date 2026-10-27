"""Acceptance contract for the axis label-ordering violations API."""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class LabelOrderingEntry(BaseModel):
    label: str
    label_index: int


class LabelOrderingViolation(BaseModel):
    server_id: str
    axis_name: str
    labels: List[LabelOrderingEntry]
    reason: str


class LabelOrderingViolationsResponse(BaseModel):
    total_rows: int
    distinct_axes: int
    violations: List[LabelOrderingViolation]
    violation_count: int


def get_label_ordering_violations(
    session: Session = Depends(get_session),
) -> LabelOrderingViolationsResponse:
    previous_label_index = func.lag(McpLlmAxisScore.label_index).over(
        partition_by=(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
        ),
        order_by=(
            McpLlmAxisScore.label,
            McpLlmAxisScore.id,
        ),
    )

    statement = (
        select(
            McpLlmAxisScore.id,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            previous_label_index.label("previous_label_index"),
        )
        .order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.id,
        )
    )
    rows = session.execute(statement).mappings().all()

    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["server_id"], row["axis_name"])].append(dict(row))

    violations: List[LabelOrderingViolation] = []
    for (server_id, axis_name), group_rows in sorted(groups.items()):
        label_indexes = [row["label_index"] for row in group_rows]
        duplicate_indexes = [
            label_index
            for label_index, count in Counter(label_indexes).items()
            if count > 1
        ]
        ordering_invalid = any(
            row["previous_label_index"] is not None
            and row["label_index"] <= row["previous_label_index"]
            for row in group_rows
        )

        reasons: List[str] = []
        if duplicate_indexes:
            reasons.append("label_index values are not unique within group")
        if ordering_invalid:
            reasons.append(
                "label_index is not strictly increasing with label ordering"
            )
        if not reasons:
            continue

        labels = [
            LabelOrderingEntry(
                label=row["label"],
                label_index=row["label_index"],
            )
            for row in sorted(
                group_rows,
                key=lambda item: (
                    item["label_index"],
                    item["label"],
                    item["id"],
                ),
            )
        ]
        violations.append(
            LabelOrderingViolation(
                server_id=server_id,
                axis_name=axis_name,
                labels=labels,
                reason="; ".join(reasons),
            )
        )

    return LabelOrderingViolationsResponse(
        total_rows=len(rows),
        distinct_axes=len({row["axis_name"] for row in rows}),
        violations=violations,
        violation_count=len(violations),
    )


router = APIRouter()


@router.get(
    "/scores/label-ordering-violations",
    response_model=LabelOrderingViolationsResponse,
)
def label_ordering_violations(
    session: Session = Depends(get_session),
) -> LabelOrderingViolationsResponse:
    return get_label_ordering_violations(session)


app = FastAPI(title="Axis Label Ordering Violations API")
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    label_index INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, label, label_index)
                VALUES
                    (1, 'clean-server', 'auth_strength', 'level_0', 0),
                    (2, 'clean-server', 'auth_strength', 'level_1', 1),
                    (3, 'clean-server', 'auth_strength', 'level_2', 2),
                    (4, 'dirty-server', 'auth_strength', 'level_0', 2),
                    (5, 'dirty-server', 'auth_strength', 'level_1', 0),
                    (6, 'dirty-server', 'auth_strength', 'level_2', 1)
                """
            )
        )

    def override_get_session() -> Iterable[Session]:
        database_session = testing_session()
        try:
            yield database_session
        finally:
            database_session.close()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scores/label-ordering-violations")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_rows"] == 6, payload
        assert payload["violation_count"] == 1, payload
        assert len(payload["violations"]) == 1, payload
        violation = payload["violations"][0]
        assert violation["server_id"] == "dirty-server", payload
        assert violation["axis_name"] == "auth_strength", payload

    engine.dispose()
    print("PASS")