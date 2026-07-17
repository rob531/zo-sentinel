from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
import requests

app = FastAPI()

class AxisSummary(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class ServerAxisSummary(BaseModel):
    server_id: str
    axes: Dict[str, AxisSummary]

def get_server_axis_summary(server_id: str, db: Session = Depends(get_session)) -> dict:
    # Verify server exists
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Query axis scores
    axis_scores = db.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.p_critical,
        MCPLLMAxisScores.p_danger
    ).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    # Format response
    summary = {
        "server_id": server_id,
        "axes": {
            axis.axis_name: {
                "label": axis.label,
                "p_top": axis.p_top,
                "p_critical": axis.p_critical,
                "p_danger": axis.p_danger
            }
            for axis in axis_scores
        }
    }

    return summary

@app.get("/server/{server_id}/axis_summary", response_model=ServerAxisSummary)
async def server_axis_summary_endpoint(server_id: str, db: Session = Depends(get_session)):
    return get_server_axis_summary(server_id, db)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Insert test data
    test_server = MCPServerRegistry(server_id="srv-1", name="Test Server")
    test_session.add(test_server)

    test_axis1 = MCPLLMAxisScores(
        server_id="srv-1",
        axis_name="axis1",
        label="Test Axis 1",
        p_top=0.9,
        p_critical=0.7,
        p_danger=0.5
    )
    test_axis2 = MCPLLMAxisScores(
        server_id="srv-1",
        axis_name="axis2",
        label="Test Axis 2",
        p_top=0.8,
        p_critical=0.6,
        p_danger=0.4
    )
    test_session.add_all([test_axis1, test_axis2])
    test_session.commit()

    # Test endpoint
    result = get_server_axis_summary("srv-1", test_session)
    assert result["server_id"] == "srv-1"
    assert len(result["axes"]) == 2
    assert result["axes"]["axis1"]["p_top"] == 0.9
    assert result["axes"]["axis2"]["p_danger"] == 0.4

    print("PASS")