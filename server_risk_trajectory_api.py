from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx

from app.db import get_session
from app.models import mcp_llm_axis_scores, mcp_server_registry

router = APIRouter(prefix="/servers", tags=["risk-trajectory"])


class AxisBreakdown(BaseModel):
    axis: str
    current: float
    baseline: float
    delta: float


class RiskTrajectoryResponse(BaseModel):
    server_id: str
    current_score: float
    baseline_score: float
    delta: float
    trajectory_label: str
    trend_direction: str
    axis_breakdown: list[AxisBreakdown]
    computed_at: datetime


def compute_trajectory_label(delta: float) -> str:
    if delta < -5:
        return "improving"
    elif delta > 5:
        return "declining"
    return "stable"


def compute_trend_direction(delta: float) -> str:
    if delta < -5:
        return "up"
    elif delta > 5:
        return "down"
    return "neutral"


def query_write_service(query: dict) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post("http://127.0.0.1:8772/query", json=query)
        response.raise_for_status()
        return response.json()


@router.get("/{server_id}/risk-trajectory", response_model=RiskTrajectoryResponse)
def get_risk_trajectory(
    server_id: str,
    session=Depends(get_session)
) -> RiskTrajectoryResponse:
    server = session.query(mcp_server_registry).filter(
        mcp_server_registry.server_id == server_id
    ).first()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    now = datetime.utcnow()
    current_start = now - timedelta(days=7)
    baseline_start = now - timedelta(days=14)
    baseline_end = current_start
    
    current_data = query_write_service({
        "table": "mcp_llm_axis_scores",
        "filter": {
            "server_id": server_id,
            "created_at": {"$gte": current_start.isoformat()}
        },
        "order_by": "created_at",
        "order": "desc",
        "limit": 100
    })
    
    baseline_data = query_write_service({
        "table": "mcp_llm_axis_scores",
        "filter": {
            "server_id": server_id,
            "created_at": {"$gte": baseline_start.isoformat(), "$lt": baseline_end.isoformat()}
        },
        "order_by": "created_at",
        "order": "desc",
        "limit": 100
    })
    
    if not current_data or not baseline_data:
        raise HTTPException(status_code=404, detail="Insufficient data for trajectory calculation")
    
    current_avg = sum(item["overall_risk_p_top"] for item in current_data) / len(current_data)
    baseline_avg = sum(item["overall_risk_p_top"] for item in baseline_data) / len(baseline_data)
    delta = current_avg - baseline_avg
    
    trajectory_label = compute_trajectory_label(delta)
    trend_direction = compute_trend_direction(delta)
    
    axis_breakdown = []
    axes = ["safety", "reliability", "cost", "performance"]
    for axis in axes:
        current_vals = [item["axis_scores"].get(axis, 0) for item in current_data]
        baseline_vals = [item["axis_scores"].get(axis, 0) for item in baseline_data]
        current_axis_avg = sum(current_vals) / len(current_vals)
        baseline_axis_avg = sum(baseline_vals) / len(baseline_vals)
        axis_delta = current_axis_avg - baseline_axis_avg
        
        axis_breakdown.append(AxisBreakdown(
            axis=axis,
            current=current_axis_avg,
            baseline=baseline_axis_avg,
            delta=axis_delta
        ))
    
    return RiskTrajectoryResponse(
        server_id=server_id,
        current_score=current_avg,
        baseline_score=baseline_avg,
        delta=delta,
        trajectory_label=trajectory_label,
        trend_direction=trend_direction,
        axis_breakdown=axis_breakdown,
        computed_at=now
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import patch, MagicMock
    
    from app.models import Base
    
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.include_router(router)
    
    mock_server = MagicMock()
    mock_server.server_id = "server-123"
    
    improving_mock_data = {
        "current_scores": [
            {
                "server_id": "server-123",
                "created_at": "2024-01-14T10:00:00",
                "overall_risk_p_top": 15.0,
                "axis_scores": {"safety": 5.0, "reliability": 5.0, "cost": 5.0}
            }
        ],
        "baseline_scores": [
            {
                "server_id": "server-123",
                "created_at": "2024-01-07T10:00:00",
                "overall_risk_p_top": 25.0,
                "axis_scores": {"safety": 8.0, "reliability": 8.0, "cost": 9.0}
            }
        ]
    }
    
    declining_mock_data = {
        "current_scores": [
            {
                "server_id": "server-456",
                "created_at": "2024-01-14T10:00:00",
                "overall_risk_p_top": 35.0,
                "axis_scores": {"safety": 12.0, "reliability": 12.0, "cost": 11.0}
            }
        ],
        "baseline_scores": [
            {
                "server_id": "server-456",
                "created_at": "2024-01-07T10:00:00",
                "overall_risk_p_top": 25.0,
                "axis_scores": {"safety": 8.0, "reliability": 8.0, "cost": 9.0}
            }
        ]
    }
    
    stable_mock_data = {
        "current_scores": [
            {
                "server_id": "server-789",
                "created_at": "2024-01-14T10:00:00",
                "overall_risk_p_top": 20.0,
                "axis_scores": {"safety": 6.0, "reliability": 7.0, "cost": 7.0}
            }
        ],
        "baseline_scores": [
            {
                "server_id": "server-789",
                "created_at": "2024-01-07T10:00:00",
                "overall_risk_p_top": 18.0,
                "axis_scores": {"safety": 6.0, "reliability": 6.0, "cost": 6.0}
            }
        ]
    }
    
    with patch("httpx.Client") as mock_httpx:
        mock_cm = MagicMock()
        mock_client = MagicMock()
        mock_httpx.return_value = mock_cm
        mock_cm.__enter__ = MagicMock(return_value=mock_client)
        mock_cm.__exit__ = MagicMock(return_value=None)
        
        client = TestClient(app, dependency_overrides={get_session: override_get_session})
        
        session = TestingSessionLocal()
        session.add(mock_server)
        session.commit()
        session.close()
        
        mock_client.post.return_value.json.return_value = improving_mock_data
        
        response = client.get("/servers/server-123/risk-trajectory")
        assert response.status_code == 200
        data = response.json()
        assert data["trajectory_label"] == "improving"
        assert data["trend_direction"] == "up"
        
        mock_client.post.return_value.json.return_value = declining_mock_data
        
        response = client.get("/servers/server-456/risk-trajectory")
        assert response.status_code == 200
        data = response.json()
        assert data["trajectory_label"] == "declining"
        assert data["trend_direction"] == "down"
        
        mock_client.post.return_value.json.return_value = stable_mock_data
        
        response = client.get("/servers/server-789/risk-trajectory")
        assert response.status_code == 200
        data = response.json()
        assert data["trajectory_label"] == "stable"
        assert data["trend_direction"] == "neutral"
    
    print("PASS")