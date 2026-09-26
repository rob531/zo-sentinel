# deps: fastapi, sqlalchemy, requests
"""Router for risk_tier_by_axis service.
Returns risk tier breakdown by LLM scoring axis for MCP servers.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_tier_by_axis"])


class AxisTier(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    risk_label: str


class ServerAxisRiskResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    registry_source: Optional[str]
    overall_risk_label: str
    axes: List[AxisTier]
    assessed_at: Optional[str]


class AxisSummary(BaseModel):
    axis_name: str
    server_count: int
    avg_p_top: Optional[float]
    dominant_label: Optional[str]


class AxisDistributionResponse(BaseModel):
    axes: List[AxisSummary]
    generated_at: str


def _risk_label(p_top: Optional[float], p_critical: Optional[float]) -> str:
    if p_critical is not None and p_critical >= 0.5:
        return "CRITICAL"
    if p_top is not None and p_top >= 0.7:
        return "HIGH"
    if p_top is not None and p_top >= 0.4:
        return "MEDIUM"
    return "LOW"


@router.get("/risk_tier_by_axis/servers/{server_id}", response_model=ServerAxisRiskResponse)
def get_server_axis_risk(
    server_id: str,
    db: Session = Depends(get_session),
):
    """Get risk tier breakdown by axis for a specific server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).order_by(McpLlmAxisScore.scored_at.desc()).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    axes: List[AxisTier] = []
    overall_p_top: Optional[float] = None
    overall_p_critical: Optional[float] = None

    for score in axis_scores:
        if score.axis_name == "overall_risk":
            overall_p_top = score.p_top
            overall_p_critical = score.p_critical
        axes.append(AxisTier(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            risk_label=_risk_label(score.p_top, score.p_critical),
        ))

    return ServerAxisRiskResponse(
        server_id=server_id,
        server_name=server.name,
        registry_source=server.registry_source,
        overall_risk_label=_risk_label(overall_p_top, overall_p_critical),
        axes=axes,
        assessed_at=axis_scores[0].scored_at.isoformat() if axis_scores and axis_scores[0].scored_at else None,
    )


@router.get("/risk_tier_by_axis/servers", response_model=List[ServerAxisRiskResponse])
def list_servers_by_axis(
    axis_name: str = Query(..., description="Filter by axis name (e.g., 'overall_risk', 'auth_strength')"),
    label: Optional[str] = Query(None, description="Filter by axis label"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    """List servers filtered by a specific axis and optional label."""
    query = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == axis_name
    )

    if label:
        query = query.filter(McpLlmAxisScore.label == label)

    axis_scores = query.order_by(McpLlmAxisScore.scored_at.desc()).limit(limit).all()

    server_ids = list(set(s.server_id for s in axis_scores))
    servers = {
        s.server_id: s
        for s in db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id.in_(server_ids)
        ).all()
    }

    results: List[ServerAxisRiskResponse] = []
    for score in axis_scores:
        if score.server_id not in servers:
            continue
        server = servers[score.server_id]
        all_axes = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == score.server_id
        ).order_by(McpLlmAxisScore.scored_at.desc()).all()

        axes = [AxisTier(
            axis_name=a.axis_name,
            label=a.label,
            p_top=a.p_top,
            p_critical=a.p_critical,
            p_danger=a.p_danger,
            risk_label=_risk_label(a.p_top, a.p_critical),
        ) for a in all_axes]

        overall_score = next((a for a in all_axes if a.axis_name == "overall_risk"), None)

        results.append(ServerAxisRiskResponse(
            server_id=score.server_id,
            server_name=server.name,
            registry_source=server.registry_source,
            overall_risk_label=_risk_label(
                overall_score.p_top if overall_score else None,
                overall_score.p_critical if overall_score else None,
            ),
            axes=axes,
            assessed_at=all_axes[0].scored_at.isoformat() if all_axes and all_axes[0].scored_at else None,
        ))

    return results


@router.get("/risk_tier_by_axis/distribution", response_model=AxisDistributionResponse)
def get_axis_distribution(
    db: Session = Depends(get_session),
):
    """Get distribution summary across all axes."""
    from sqlalchemy import func

    axis_stats = db.query(
        McpLlmAxisScore.axis_name,
        func.count(McpLlmAxisScore.id).label("server_count"),
        func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
    ).group_by(McpLlmAxisScore.axis_name).all()

    dominant_labels = db.query(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        func.count(McpLlmAxisScore.id).label("cnt"),
    ).group_by(
        McpLlmAxisScore.axis_name, McpLlmAxisScore.label
    ).order_by(
        McpLlmAxisScore.axis_name, func.count(McpLlmAxisScore.id).desc()
    ).all()

    label_map: dict = {}
    for row in dominant_labels:
        if row.axis_name not in label_map:
            label_map[row.axis_name] = row.label

    axes = [AxisSummary(
        axis_name=stat.axis_name,
        server_count=stat.server_count,
        avg_p_top=float(stat.avg_p_top) if stat.avg_p_top else None,
        dominant_label=label_map.get(stat.axis_name),
    ) for stat in axis_stats]

    return AxisDistributionResponse(
        axes=axes,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: TestSession()

    session = TestSession()
    from app.models import Org

    org = Org(id="org-test-1", name="Test Org")
    session.add(org)

    servers = [
        McpServerRegistry(
            server_id="srv-axis-1",
            name="Auth Server",
            registry_source="npm",
            risk_tier="HIGH",
        ),
        McpServerRegistry(
            server_id="srv-axis-2",
            name="Safe Server",
            registry_source="npm",
            risk_tier="LOW",
        ),
    ]
    session.add_all(servers)

    now = datetime.utcnow()
    scores = [
        McpLlmAxisScore(
            server_id="srv-axis-1", axis_name="overall_risk",
            label="HIGH", label_index=2, p_top=0.75, p_critical=0.1, p_danger=0.2,
            model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-axis-1", axis_name="auth_strength",
            label="MEDIUM", label_index=1, p_top=0.55, p_critical=0.05, p_danger=0.1,
            model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-axis-2", axis_name="overall_risk",
            label="LOW", label_index=0, p_top=0.2, p_critical=0.01, p_danger=0.05,
            model_version="v1", scored_at=now,
        ),
    ]
    session.add_all(scores)
    session.commit()

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/risk_tier_by_axis/servers/srv-axis-1")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == "srv-axis-1"
    assert len(data["axes"]) == 2
    assert data["overall_risk_label"] == "HIGH"
    print(f"Response keys: {list(data.keys())}")

    resp2 = client.get("/api/risk_tier_by_axis/servers", params={"axis_name": "overall_risk", "limit": 10})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2) == 2
    print(f"Servers with overall_risk: {len(data2)}")

    resp3 = client.get("/api/risk_tier_by_axis/distribution")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["axes"]) == 2
    print(f"Axes in distribution: {len(data3['axes'])}")

    print("Self-test passed")
    sys.exit(0)
