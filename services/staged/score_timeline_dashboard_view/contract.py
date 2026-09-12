from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisScoreDetail(BaseModel):
    p_top: float
    p_critical: float
    p_danger: float
    label: str
    tier: str


class TimelinePoint(BaseModel):
    scored_at: datetime
    axes: Dict[str, AxisScoreDetail]


class ScoreTimelineResponse(BaseModel):
    server_id: str
    days: int
    series: List[TimelinePoint]


def compute_tier(p_top: float, p_critical: float, p_danger: float) -> str:
    if p_top >= 0.7:
        return "top"
    elif p_critical >= 0.5:
        return "critical"
    elif p_danger >= 0.3:
        return "danger"
    return "normal"


def get_score_timeline(
    server_id: str,
    days: int,
    session: Session = Depends(get_session)
) -> ScoreTimelineResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= cutoff
    ).order_by(McpLlmAxisScore.scored_at).all()
    
    series_map: Dict[datetime, Dict[str, AxisScoreDetail]] = {}
    
    for score in scores:
        scored_at = score.scored_at
        if scored_at not in series_map:
            series_map[scored_at] = {}
        
        p_top = float(score.p_top) if score.p_top is not None else 0.0
        p_critical = float(score.p_critical) if score.p_critical is not None else 0.0
        p_danger = float(score.p_danger) if score.p_danger is not None else 0.0
        label = score.label or ""
        
        tier = compute_tier(p_top, p_critical, p_danger)
        
        series_map[scored_at][score.axis_name] = AxisScoreDetail(
            p_top=p_top,
            p_critical=p_critical,
            p_danger=p_danger,
            label=label,
            tier=tier
        )
    
    series = [
        TimelinePoint(scored_at=ts, axes=axes)
        for ts, axes in sorted(series_map.items())
    ]
    
    return ScoreTimelineResponse(
        server_id=server_id,
        days=days,
        series=series
    )


def create_app() -> FastAPI:
    app = FastAPI()
    
    @app.get("/api/scores/timeline/{server_id}", response_model=ScoreTimelineResponse)
    def get_timeline(
        server_id: str,
        days: int = 7,
        session: Session = Depends(get_session)
    ):
        return get_score_timeline(server_id, days, session)
    
    return app


def _run_self_test():
    from testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    metadata = McpLlmAxisScore.metadata
    metadata.create_all(engine)
    
    SessionLocal = sessionmaker(bind=engine)
    
    base_time = datetime.utcnow()
    
    with SessionLocal() as db:
        for i, server_num in enumerate(["S001", "S002"]):
            for snapshot_idx in range(3):
                day_offset = snapshot_idx % 2
                scored_at = base_time - timedelta(days=day_offset)
                
                for axis_name in ["safety", "reliability", "compliance"]:
                    score = McpLlmAxisScore(
                        id=f"id_{server_num}_{snapshot_idx}_{axis_name}",
                        server_id=server_num,
                        axis_name=axis_name,
                        scored_at=scored_at,
                        p_top=0.5 + (snapshot_idx * 0.1),
                        p_critical=0.2 + (snapshot_idx * 0.05),
                        p_danger=0.1 + (snapshot_idx * 0.02),
                        label=f"{axis_name}_label_{snapshot_idx}",
                        adapter_sha256="abc123",
                        decision_rule_version="v1",
                        model_version="v1",
                        probs=None,
                        escalated=False,
                        escalated_to=None,
                        label_index=snapshot_idx
                    )
                    db.add(score)
        
        db.commit()
    
    def override_get_session():
        return SessionLocal()
    
    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response1 = client.get("/api/scores/timeline/S001?days=7")
    assert response1.status_code == 200, f"Expected 200, got {response1.status_code}"
    
    data1 = response1.json()
    assert data1["server_id"] == "S001"
    assert len(data1["series"]) >= 2, f"Expected series length >= 2, got {len(data1['series'])}"
    
    for point in data1["series"]:
        assert "scored_at" in point
        assert "axes" in point
    
    response2 = client.get("/api/scores/timeline/S002?days=7")
    assert response2.status_code == 200
    
    data2 = response2.json()
    assert data2["server_id"] == "S002"
    assert len(data2["series"]) >= 2
    
    print("PASS")


if __name__ == "__main__":
    _run_self_test()