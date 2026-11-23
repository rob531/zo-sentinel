from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

CANONICAL_AXES: List[str] = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

CANONICAL_AXIS_SET = set(CANONICAL_AXES)
CANONICAL_LABEL_BY_INDEX = {
    index: axis for index, axis in enumerate(CANONICAL_AXES)
}

router = APIRouter(prefix="/api")


class AxisPresenceResponse(BaseModel):
    servers: int
    coverage_pct: float


class AxisLabelGapRow(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    axes_present: int
    missing_axes: List[str]
    mislabelled: bool


class AxisLabelGapResponse(BaseModel):
    total_servers: int
    incomplete: int
    mislabelled: int
    axis_presence: Dict[str, AxisPresenceResponse]
    rows: List[AxisLabelGapRow]
    limit: int
    offset: int


def get_axis_label_gaps(
    db: Session,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, object]:
    """Audit canonical-axis coverage and score labelling for scored servers."""
    score_rows = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
    ).all()

    scores_by_server: Dict[str, List[tuple[str, object, object]]] = {}
    for server_id, axis_name, label, label_index in score_rows:
        normalized_server_id = str(server_id)
        scores_by_server.setdefault(normalized_server_id, []).append(
            (axis_name, label, label_index)
        )

    server_ids = list(scores_by_server)
    registry_by_server: Dict[str, McpServerRegistry] = {}
    if server_ids:
        registry_rows = db.execute(
            select(McpServerRegistry).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).scalars()
        registry_by_server = {
            str(server.server_id): server for server in registry_rows
        }

    total_servers = len(scores_by_server)
    axis_server_sets: Dict[str, set[str]] = {
        axis: set() for axis in CANONICAL_AXES
    }
    incomplete = 0
    mislabelled_servers = 0
    report_rows: List[AxisLabelGapRow] = []

    for server_id in sorted(scores_by_server):
        scores = scores_by_server[server_id]
        present_axes = {
            str(axis_name)
            for axis_name, _label, _label_index in scores
            if axis_name in CANONICAL_AXIS_SET
        }
        missing_axes = [
            axis for axis in CANONICAL_AXES if axis not in present_axes
        ]
        server_mislabelled = False

        for axis_name, label, label_index in scores:
            if axis_name in CANONICAL_AXIS_SET:
                axis_server_sets[str(axis_name)].add(server_id)

            index_is_valid = (
                label_index is not None
                and isinstance(label_index, int)
                and 0 <= label_index <= 6
            )
            canonical_label = (
                CANONICAL_LABEL_BY_INDEX.get(label_index) if index_is_valid else None
            )
            if (
                not index_is_valid
                or label != canonical_label
                or axis_name != canonical_label
            ):
                server_mislabelled = True

        if missing_axes:
            incomplete += 1
        if server_mislabelled:
            mislabelled_servers += 1

        registry = registry_by_server.get(server_id)
        report_rows.append(
            AxisLabelGapRow(
                server_id=server_id,
                name=str(registry.name) if registry and registry.name else server_id,
                risk_tier=(
                    str(registry.risk_tier)
                    if registry and registry.risk_tier
                    else "unknown"
                ),
                axes_present=len(present_axes),
                missing_axes=missing_axes,
                mislabelled=server_mislabelled,
            )
        )

    axis_presence: Dict[str, AxisPresenceResponse] = {}
    for axis in CANONICAL_AXES:
        servers = len(axis_server_sets[axis])
        coverage_pct = (
            round((servers / total_servers) * 100.0, 2)
            if total_servers
            else 0.0
        )
        axis_presence[axis] = AxisPresenceResponse(
            servers=servers,
            coverage_pct=coverage_pct,
        )

    return {
        "total_servers": total_servers,
        "incomplete": incomplete,
        "mislabelled": mislabelled_servers,
        "axis_presence": axis_presence,
        "rows": report_rows[offset : offset + limit],
        "limit": limit,
        "offset": offset,
    }


def build_axis_label_gap_report(
    db: Session,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, object]:
    """Compatibility alias for callers that use report-oriented naming."""
    return get_axis_label_gaps(db, limit=limit, offset=offset)


@router.get(
    "/scoring/axis-label-gaps",
    response_model=AxisLabelGapResponse,
)
def axis_label_index_gap_report(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Return axis completeness and labelling diagnostics."""
    return get_axis_label_gaps(db, limit=limit, offset=offset)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(test_engine, checkfirst=True)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="complete-server",
                    name="Complete Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="incomplete-server",
                    name="Incomplete Server",
                    risk_tier="medium",
                ),
            ]
        )
        db.flush()

        complete_scores = [
            McpLlmAxisScore(
                id=index + 1,
                server_id="complete-server",
                axis_name=axis,
                label=axis,
                label_index=index,
                model_version="test-model",
            )
            for index, axis in enumerate(CANONICAL_AXES)
        ]
        incomplete_axes = CANONICAL_AXES[:4]
        incomplete_scores = [
            McpLlmAxisScore(
                id=100 + index,
                server_id="incomplete-server",
                axis_name=axis,
                label=axis,
                label_index=9 if index == 0 else index,
                model_version="test-model",
            )
            for index, axis in enumerate(incomplete_axes)
        ]
        db.add_all(complete_scores + incomplete_scores)
        db.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(that_app) as client:
        response = client.get(
            "/api/scoring/axis-label-gaps",
            params={"limit": 50, "offset": 0},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["incomplete"] == 1
        assert payload["total_servers"] == 2
        assert set(payload["axis_presence"]) == set(CANONICAL_AXES)
        assert payload["mislabelled"] == 1

    print("PASS")