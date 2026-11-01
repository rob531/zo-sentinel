"""Logic for the axis coverage matrix service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_coverage_matrix(
    db: Session = Depends(get_session),
    axis_name: Optional[str] = None,
    server_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return per-axis coverage for the registered MCP server population."""
    if axis_name is not None and axis_name not in AXES:
        raise ValueError(f"Unsupported axis: {axis_name}")

    registry_query = select(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
    )
    if server_id is not None:
        registry_query = registry_query.where(McpServerRegistry.server_id == server_id)

    registry_rows = db.execute(registry_query).all()
    total_servers = len(registry_rows)

    score_query = (
        select(
            McpLlmAxisScore.axis_name,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "scored_servers"
            ),
            func.count(
                func.distinct(McpLlmAxisScore.server_id)
            ).filter(McpLlmAxisScore.escalated.is_(True)).label("escalated"),
            func.avg(McpLlmAxisScore.p_top).label("mean_p_top"),
            func.max(McpLlmAxisScore.p_top).label("max_p_top"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )
    if axis_name is not None:
        score_query = score_query.where(McpLlmAxisScore.axis_name == axis_name)
    if server_id is not None:
        score_query = score_query.where(McpLlmAxisScore.server_id == server_id)

    aggregate_rows = db.execute(
        score_query.group_by(McpLlmAxisScore.axis_name)
    ).all()
    aggregates = {row.axis_name: row for row in aggregate_rows}

    versions_query = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.decision_rule_version,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.decision_rule_version.is_not(None))
        .distinct()
    )
    if axis_name is not None:
        versions_query = versions_query.where(McpLlmAxisScore.axis_name == axis_name)
    if server_id is not None:
        versions_query = versions_query.where(McpLlmAxisScore.server_id == server_id)

    version_rows = db.execute(versions_query).all()
    versions: Dict[str, List[str]] = {}
    for row in version_rows:
        versions.setdefault(row.axis_name, []).append(str(row.decision_rule_version))
    for axis_versions in versions.values():
        axis_versions.sort()

    completeness_query = (
        select(
            McpLlmAxisScore.server_id,
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )
    if server_id is not None:
        completeness_query = completeness_query.where(
            McpLlmAxisScore.server_id == server_id
        )

    completeness_rows = db.execute(
        completeness_query.group_by(McpLlmAxisScore.server_id)
    ).all()
    servers_with_all_axes = sum(
        1 for row in completeness_rows if int(row.axis_count) == len(AXES)
    )
    servers_missing_any_axis = max(
        0, total_servers - servers_with_all_axes
    )

    output_axes = AXES if axis_name is None else (axis_name,)
    axes = []
    for current_axis in output_axes:
        aggregate = aggregates.get(current_axis)
        scored_servers = int(aggregate.scored_servers) if aggregate else 0
        coverage_pct = (
            float(scored_servers) * 100.0 / float(total_servers)
            if total_servers
            else 0.0
        )
        axes.append(
            {
                "axis_name": current_axis,
                "scored_servers": scored_servers,
                "coverage_pct": coverage_pct,
                "escalated": int(aggregate.escalated) if aggregate else 0,
                "mean_p_top": (
                    float(aggregate.mean_p_top)
                    if aggregate and aggregate.mean_p_top is not None
                    else 0.0
                ),
                "max_p_top": (
                    float(aggregate.max_p_top)
                    if aggregate and aggregate.max_p_top is not None
                    else 0.0
                ),
                "decision_rule_versions": versions.get(current_axis, []),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "axes": axes,
        "servers_with_all_7_axes": servers_with_all_axes,
        "servers_missing_any_axis": servers_missing_any_axis,
    }


def coverage_matrix(
    db: Session = Depends(get_session),
    axis_name: Optional[str] = None,
    server_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility entry point for the axis coverage matrix."""
    return get_coverage_matrix(db, axis_name=axis_name, server_id=server_id)


def get_axis_coverage_matrix(
    db: Session = Depends(get_session),
    axis_name: Optional[str] = None,
    server_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility entry point for the axis coverage matrix."""
    return get_coverage_matrix(db, axis_name=axis_name, server_id=server_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SessionLocal
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    with SessionLocal(bind=engine) as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", risk_tier="high"),
                McpServerRegistry(server_id="server-2", risk_tier="medium"),
                McpServerRegistry(server_id="server-3", risk_tier="low"),
            ]
        )
        score_id = 1
        for current_server in ("server-1", "server-2"):
            for current_axis in AXES:
                session.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=current_server,
                        axis_name=current_axis,
                        model_version="test-model",
                        p_top=0.5,
                        escalated=(current_server == "server-2" and current_axis == "overall_risk"),
                        decision_rule_version="v1",
                    )
                )
                score_id += 1
        for current_axis in AXES[:5]:
            session.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id="server-3",
                    axis_name=current_axis,
                    model_version="test-model",
                    p_top=0.25,
                    escalated=False,
                    decision_rule_version="v1",
                )
            )
            score_id += 1
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with SessionLocal(bind=engine) as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/axis/coverage-matrix")
    def coverage_matrix_endpoint(db: Session = Depends(get_session)):
        return get_coverage_matrix(db)

    response = TestClient(that_app).get("/api/axis/coverage-matrix")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["axes"]) == 7
    assert isinstance(payload["axes"][0]["coverage_pct"], float)
    assert 0 <= payload["axes"][0]["coverage_pct"] <= 100
    assert payload["servers_missing_any_axis"] == 1
    print("PASS")