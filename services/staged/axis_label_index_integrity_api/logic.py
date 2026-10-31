from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
CANONICAL_AXIS_SET = frozenset(CANONICAL_AXES)


def _coerce_label_index(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def audit_label_indexes(rows: List[dict]) -> dict:
    grouped: Dict[Any, List[dict]] = {}
    for row in rows:
        axis_name = row.get("axis_name")
        grouped.setdefault(axis_name, []).append(row)

    canonical_rank = {axis: index for index, axis in enumerate(CANONICAL_AXES)}
    first_seen = {axis_name: index for index, axis_name in enumerate(grouped)}
    axis_names = sorted(
        grouped,
        key=lambda axis_name: (
            canonical_rank.get(axis_name, len(CANONICAL_AXES)),
            first_seen[axis_name],
        ),
    )

    axes = []
    gaps_total = 0

    for axis_name in axis_names:
        axis_rows = grouped[axis_name]
        raw_indexes = [row.get("label_index") for row in axis_rows]
        indexes = [_coerce_label_index(value) for value in raw_indexes]
        valid_indexes = [value for value in indexes if value is not None]

        counts = Counter(valid_indexes)
        duplicate_indexes = sorted(
            index for index, count in counts.items() if count > 1
        )

        if valid_indexes:
            min_index = min(valid_indexes)
            max_index = max(valid_indexes)
            missing_indexes = sorted(
                set(range(0, max_index + 1)).difference(valid_indexes)
            )
        else:
            min_index = None
            max_index = None
            missing_indexes = []

        ordered = (
            bool(indexes)
            and len(valid_indexes) == len(indexes)
            and indexes == sorted(indexes)
        )

        missing_count = len(missing_indexes)
        gaps_total += missing_count

        axes.append(
            {
                "axis_name": axis_name,
                "expected_axis": axis_name in CANONICAL_AXIS_SET,
                "rows": len(axis_rows),
                "labels": [row.get("label") for row in axis_rows],
                "missing_indexes": missing_indexes,
                "duplicate_indexes": duplicate_indexes,
                "min_index": min_index,
                "max_index": max_index,
                "ordered": ordered,
            }
        )

    return {"axes": axes, "gaps_total": gaps_total}


def load_axis_score_rows(session) -> List[dict]:
    records = (
        session.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
        )
        .order_by(
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        )
        .all()
    )

    return [
        {
            "axis_name": record.axis_name,
            "label": record.label,
            "label_index": record.label_index,
            "scored_at": record.scored_at,
            "server_id": record.server_id,
        }
        for record in records
    ]


def get_axis_label_index_integrity(session) -> dict:
    result = audit_label_indexes(load_axis_score_rows(session))
    result["checked_axes"] = len(result["axes"])
    return result


def get_axis_label_integrity(session) -> dict:
    return get_axis_label_index_integrity(session)


def axis_label_integrity_endpoint(session=Depends(get_session)) -> dict:
    return get_axis_label_index_integrity(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    scored_at TEXT,
                    server_id INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, axis_name, label, label_index, scored_at, server_id)
                VALUES
                    (1, 'overall_risk', 'informational', 0,
                     '2024-01-01T00:00:00', 1),
                    (2, 'overall_risk', 'low', 1,
                     '2024-01-01T00:01:00', 1),
                    (3, 'overall_risk', 'medium', 2,
                     '2024-01-01T00:02:00', 1),
                    (4, 'overall_risk', 'high', 3,
                     '2024-01-01T00:03:00', 1),
                    (5, 'auth_strength', 'informational', 0,
                     '2024-01-02T00:00:00', 2),
                    (6, 'auth_strength', 'medium', 2,
                     '2024-01-02T00:01:00', 2),
                    (7, 'auth_strength', 'medium', 2,
                     '2024-01-02T00:02:00', 2),
                    (8, 'auth_strength', 'high', 3,
                     '2024-01-02T00:03:00', 2),
                    (9, 'unexpected_axis', 'low', 0,
                     '2024-01-03T00:00:00', 3)
                """
            )
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/scores/axis-label-integrity")
    def test_endpoint(session=Depends(get_session)):
        return get_axis_label_index_integrity(session)

    with TestClient(that_app) as client:
        response = client.get("/scores/axis-label-integrity")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 3

        axes_by_name = {axis["axis_name"]: axis for axis in payload["axes"]}
        assert axes_by_name["unexpected_axis"]["expected_axis"] is False
        assert axes_by_name["overall_risk"]["ordered"] is True
        assert axes_by_name["auth_strength"]["duplicate_indexes"]
        assert axes_by_name["auth_strength"]["missing_indexes"]

    print("PASS")