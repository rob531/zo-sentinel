from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from datetime import datetime

class AxisScore(BaseModel):
    label: str
    score: float
    label_index: int

class ConsumeRequest(BaseModel):
    server_id: str
    axis_scores: List[AxisScore]

class ConsumeResponse(BaseModel):
    server_id: str
    risk_tier: str
    axis_count: int
    consumed_at: datetime

class ErrorResponse(BaseModel):
    error: str

def determine_risk_tier(axis_scores: List[AxisScore]) -> str:
    critical_axes = [
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]

    for axis in axis_scores:
        if axis.label in critical_axes and axis.score >= 0.8:
            return "HIGH_RISK_ISOLATED"

    overall_risk = next(
        (axis.score for axis in axis_scores if axis.label == "overall_risk"),
        0.0
    )

    if overall_risk >= 0.8:
        return "HIGH_RISK_ISOLATED"
    elif overall_risk >= 0.6:
        return "HIGH_RISK_MONITORED"
    elif overall_risk >= 0.4:
        return "MEDIUM_RISK"
    elif overall_risk >= 0.2:
        return "LOW_RISK"
    else:
        return "MINIMAL_RISK"

def consume_scoring(
    request: ConsumeRequest,
    db: Session = Depends(get_session)
) -> ConsumeResponse:
    try:
        server = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == request.server_id
        ).first()

        if not server:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found"
            )

        axis_scores = request.axis_scores
        risk_tier = determine_risk_tier(axis_scores)

        server.risk_tier = risk_tier
        db.commit()

        return ConsumeResponse(
            server_id=request.server_id,
            risk_tier=risk_tier,
            axis_count=len(axis_scores),
            consumed_at=datetime.utcnow()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

app = FastAPI()

app.post("/internal/scoring/consume")(consume_scoring)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(test_engine)

    def override_get_session() -> Session:
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_server_id = "test-server-123"
    test_axis_scores = [
        {"label": "auth_strength", "score": 0.7, "label_index": 0},
        {"label": "capability_breadth", "score": 0.5, "label_index": 1},
        {"label": "data_sensitivity", "score": 0.6, "label_index": 2},
        {"label": "network_egress", "score": 0.4, "label_index": 3},
        {"label": "maintainer_trust", "score": 0.3, "label_index": 4},
        {"label": "exploit_surface", "score": 0.2, "label_index": 5},
        {"label": "overall_risk", "score": 0.5, "label_index": 6}
    ]

    with Session(test_engine) as session:
        session.add(McpServerRegistry(
            server_id=test_server_id,
            risk_tier="UNKNOWN"
        ))
        session.commit()

    response = client.post(
        "/internal/scoring/consume",
        json={"server_id": test_server_id, "axis_scores": test_axis_scores}
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["server_id"] == test_server_id
    assert response_data["risk_tier"] in [
        "HIGH_RISK_ISOLATED",
        "HIGH_RISK_MONITORED",
        "MEDIUM_RISK",
        "LOW_RISK",
        "MINIMAL_RISK"
    ]
    assert response_data["axis_count"] == len(test_axis_scores)

    print("PASS")