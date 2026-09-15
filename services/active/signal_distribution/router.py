# deps: fastapi, sqlalchemy, pydantic, requests
"""Signal Distribution Service - provides signal scores distribution analytics."""
from __future__ import annotations

from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/api", tags=["signal_distribution"])


class SignalDistributionItem(BaseModel):
    server_id: str
    signal_name: str
    score: float
    timestamp: Optional[str] = None


class AxisScoreDistribution(BaseModel):
    server_id: str
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    model_version: str
    scored_at: Optional[str] = None


class DisputeDistribution(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    reason_category: str
    status: str
    created_at: Optional[str] = None


class SignalDistributionResponse(BaseModel):
    total_count: int
    distribution: List[SignalDistributionItem]


class AxisDistributionResponse(BaseModel):
    total_count: int
    distribution: List[AxisScoreDistribution]


class DisputeDistributionResponse(BaseModel):
    total_count: int
    distribution: List[DisputeDistribution]


def _query_mesh(sql: str, params: dict) -> dict:
    """Query mesh tables via write_service."""
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params},
            timeout=10,
        )
        if resp.status_code >= 500:
            raise HTTPException(status_code=502, detail="Mesh service unavailable")
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Mesh query failed: {str(e)}")


@router.get("/signal_distribution", response_model=SignalDistributionResponse)
def get_signal_distribution(
    org_id: Optional[str] = Query(default=None),
    server_id: Optional[str] = Query(default=None),
    signal_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> SignalDistributionResponse:
    """Get signal scores distribution from mesh table mcp_signal_scores."""
    conditions = []
    params: dict = {}

    if org_id:
        conditions.append("server_id IN (SELECT server_id FROM mcp_server_registry WHERE org_id = :org_id)")
        params["org_id"] = org_id
    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if signal_name:
        conditions.append("signal_name = :signal_name")
        params["signal_name"] = signal_name

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT server_id, signal_name, score, timestamp
        FROM mcp_signal_scores
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT :limit
    """
    params["limit"] = limit

    rows = _query_mesh(sql, params)
    items = [
        SignalDistributionItem(
            server_id=r.get("server_id", ""),
            signal_name=r.get("signal_name", ""),
            score=float(r.get("score", 0)),
            timestamp=r.get("timestamp"),
        )
        for r in rows
    ]
    return SignalDistributionResponse(total_count=len(items), distribution=items)


@router.get("/signal_distribution/axis", response_model=AxisDistributionResponse)
def get_axis_distribution(
    db: Session = Depends(get_session),
    org_id: Optional[str] = Query(default=None),
    server_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> AxisDistributionResponse:
    """Get LLM axis scores distribution from app tables."""
    query = db.query(McpLlmAxisScore)

    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    if org_id:
        query = query.join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        query = query.filter(McpServerRegistry.org_id == org_id)

    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).limit(limit).all()
    items = [
        AxisScoreDistribution(
            server_id=r.server_id,
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            model_version=r.model_version,
            scored_at=r.scored_at.isoformat() if r.scored_at else None,
        )
        for r in rows
    ]
    return AxisDistributionResponse(total_count=len(items), distribution=items)


@router.get("/signal_distribution/disputes", response_model=DisputeDistributionResponse)
def get_dispute_distribution(
    db: Session = Depends(get_session),
    org_id: Optional[str] = Query(default=None),
    server_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> DisputeDistributionResponse:
    """Get score disputes distribution from app tables."""
    query = db.query(McpScoreDispute)

    if server_id:
        query = query.filter(McpScoreDispute.server_id == server_id)
    if org_id:
        query = query.join(
            McpServerRegistry, McpScoreDispute.server_id == McpServerRegistry.server_id
        )
        query = query.filter(McpServerRegistry.org_id == org_id)
    if status_filter:
        query = query.filter(McpScoreDispute.status == status_filter)

    rows = query.order_by(McpScoreDispute.created_at.desc()).limit(limit).all()
    items = [
        DisputeDistribution(
            id=r.id,
            server_id=r.server_id,
            submitted_by=r.submitted_by,
            proposed_overall_risk=r.proposed_overall_risk,
            reason_category=r.reason_category,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
    return DisputeDistributionResponse(total_count=len(items), distribution=items)


if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
    from app.main import app

    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine("sqlite:///:memory:")
        from app.db import Base
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    def _test_signal_distribution():
        resp = client.get("/api/signal_distribution?limit=10")
        assert resp.status_code in (200, 502)
        print("_test_signal_distribution passed")

    def _test_axis_distribution():
        resp = client.get("/api/signal_distribution/axis?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_count" in data
        print("_test_axis_distribution passed")

    def _test_dispute_distribution():
        resp = client.get("/api/signal_distribution/disputes?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_count" in data
        print("_test_dispute_distribution passed")

    _test_signal_distribution()
    _test_axis_distribution()
    _test_dispute_distribution()
    print("All self-tests passed")
