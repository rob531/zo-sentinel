from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func
import statistics
from datetime import datetime

router = APIRouter()

class ServerVarianceReport(BaseModel):
    server_id: str
    name: str
    axis_name: str
    mean_p_top: float
    variance: float
    sample_count: int
    last_scored: datetime
    risk_tier: str

class Summary(BaseModel):
    total_servers_analyzed: int
    servers_flagged: int
    flagged_by_axis: dict

class VarianceReportResponse(BaseModel):
    servers: List[ServerVarianceReport]
    summary: Summary

def calculate_risk_tier(variance: float, threshold: float) -> str:
    if variance > threshold * 2:
        return "high"
    elif variance > threshold:
        return "medium"
    else:
        return "low"

def get_axis_scores(db: Session, server_id: str, axis_name: str) -> List[McpLlmAxisScores]:
    return db.query(McpLlmAxisScores).filter(
        McpLlmAxisScores.server_id == server_id,
        McpLlmAxisScores.axis_name == axis_name
    ).all()

def get_server_metadata(db: Session, server_id: str) -> McpServerRegistry:
    return db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

@router.get("/reports/axis-score-variance", response_model=VarianceReportResponse)
async def get_axis_score_variance_report(
    limit: int = 50,
    min_sample_size: int = 2,
    variance_threshold: float = 0.05,
    db: Session = Depends(get_session)
):
    try:
        # Get all unique server_id and axis_name combinations
        servers_axes = db.query(
            McpLlmAxisScores.server_id,
            McpLlmAxisScores.axis_name
        ).group_by(
            McpLlmAxisScores.server_id,
            McpLlmAxisScores.axis_name
        ).all()

        servers = []
        flagged_by_axis = {}

        for server_id, axis_name in servers_axes:
            scores = get_axis_scores(db, server_id, axis_name)

            if len(scores) < min_sample_size:
                continue

            p_tops = [score.p_top for score in scores]
            mean_p_top = statistics.mean(p_tops)
            variance = statistics.variance(p_tops)
            last_scored = max(score.scored_at for score in scores)

            server_metadata = get_server_metadata(db, server_id)
            name = server_metadata.name if server_metadata else "Unknown"

            risk_tier = calculate_risk_tier(variance, variance_threshold)

            server_report = ServerVarianceReport(
                server_id=server_id,
                name=name,
                axis_name=axis_name,
                mean_p_top=mean_p_top,
                variance=variance,
                sample_count=len(scores),
                last_scored=last_scored,
                risk_tier=risk_tier
            )

            servers.append(server_report)

            if risk_tier != "low":
                flagged_by_axis[axis_name] = flagged_by_axis.get(axis_name, 0) + 1

        # Sort by variance descending
        servers.sort(key=lambda x: x.variance, reverse=True)

        # Apply limit
        servers = servers[:limit]

        summary = Summary(
            total_servers_analyzed=len(servers_axes),
            servers_flagged=sum(1 for s in servers if s.risk_tier != "low"),
            flagged_by_axis=flagged_by_axis
        )

        return VarianceReportResponse(servers=servers, summary=summary)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_server = McpServerRegistry(
        server_id="test-server-1",
        name="Test Server 1",
        confidence=0.9
    )
    test_session.add(test_server)
    test_session.commit()

    test_scores = [
        McpLlmAxisScores(
            server_id="test-server-1",
            axis_name="test-axis-1",
            p_top=0.8,
            scored_at=datetime.now()
        ),
        McpLlmAxisScores(
            server_id="test-server-1",
            axis_name="test-axis-1",
            p_top=0.85,
            scored_at=datetime.now()
        ),
        McpLlmAxisScores(
            server_id="test-server-1",
            axis_name="test-axis-1",
            p_top=0.9,
            scored_at=datetime.now()
        ),
        McpLlmAxisScores(
            server_id="test-server-1",
            axis_name="test-axis-2",
            p_top=0.7,
            scored_at=datetime.now()
        ),
        McpLlmAxisScores(
            server_id="test-server-1",
            axis_name="test-axis-2",
            p_top=0.75,
            scored_at=datetime.now()
        ),
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Run test
    client = TestClient(app)
    response = client.get("/reports/axis-score-variance?limit=10&min_sample_size=2&variance_threshold=0.01")

    assert response.status_code == 200
    data = response.json()

    # Check variance is non-negative
    for server in data["servers"]:
        assert server["variance"] >= 0

    # Check flagged servers exceed threshold
    for server in data["servers"]:
        if server["risk_tier"] != "low":
            assert server["variance"] > 0.01

    # Check summary counts are consistent
    assert data["summary"]["total_servers_analyzed"] == 2
    assert data["summary"]["servers_flagged"] == 2
    assert data["summary"]["flagged_by_axis"]["test-axis-1"] == 1
    assert data["summary"]["flagged_by_axis"]["test-axis-2"] == 1

    print("PASS")