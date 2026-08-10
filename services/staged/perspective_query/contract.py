from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from uuid import UUID
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, PerspectiveSnapshot

router = APIRouter(prefix="/api/perspective")

def get_risk_tier(score: float) -> str:
    if score >= 0.8:
        return "Critical"
    elif score >= 0.6:
        return "High"
    elif score >= 0.4:
        return "Medium"
    elif score >= 0.2:
        return "Low"
    else:
        return "Minimal"

@router.get("/query")
async def get_perspective_query(
    perspective_id: str = Query(..., description="UUID of the perspective"),
    server_id: str = Query(..., description="ID of the server"),
    session: Session = Depends(get_session),
) -> Dict:
    # Query the database for the perspective and server
    perspective = (
        session.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .first()
    )
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Query the axis scores for the perspective and server
    axis_scores = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.perspective_id == perspective_id,
            McpLlmAxisScore.server_id == server_id,
        )
        .all()
    )

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Axis scores not found")

    # Calculate overall risk score and tier
    overall_risk = sum(score.score for score in axis_scores) / len(axis_scores)
    risk_tier = get_risk_tier(overall_risk)

    # Format the response
    axes = {
        score.axis_name: {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
        }
        for score in axis_scores
    }

    return {
        "perspective_id": perspective_id,
        "server_id": server_id,
        "axes": axes,
        "overall_risk": overall_risk,
        "risk_tier": risk_tier,
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed the database with test data
    session = SessionLocal()
    try:
        # Add test data for McpServerRegistry
        session.execute(
            "INSERT INTO McpServerRegistry (server_id, org_id, name) VALUES ('server1', 'org1', 'Server 1')"
        )

        # Add test data for perspective_snapshots
        session.execute(
            "INSERT INTO perspective_snapshots (perspective_id, org_id, name) VALUES ('123e4567-e89b-12d3-a456-426614174000', 'org1', 'Perspective 1')"
        )

        # Add test data for McpLlmAxisScore
        session.execute(
            """
            INSERT INTO McpLlmAxisScore (perspective_id, server_id, axis_name, label, p_top, p_critical, p_danger, score)
            VALUES
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis1', 'Label 1', 0.9, 0.8, 0.7, 0.85),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis2', 'Label 2', 0.8, 0.7, 0.6, 0.75),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis3', 'Label 3', 0.7, 0.6, 0.5, 0.65),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis4', 'Label 4', 0.6, 0.5, 0.4, 0.55),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis5', 'Label 5', 0.5, 0.4, 0.3, 0.45),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis6', 'Label 6', 0.4, 0.3, 0.2, 0.35),
                ('123e4567-e89b-12d3-a456-426614174000', 'server1', 'axis7', 'Label 7', 0.3, 0.2, 0.1, 0.25)
            """
        )
        session.commit()
    finally:
        session.close()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(
        "/api/perspective/query?perspective_id=123e4567-e89b-12d3-a456-426614174000&server_id=server1"
    )

    # Assert the response
    assert response.status_code == 200
    data = response.json()
    assert data["perspective_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert data["server_id"] == "server1"
    assert len(data["axes"]) == 7
    assert data["axes"]["axis1"]["p_top"] == 0.9
    assert data["axes"]["axis2"]["p_top"] == 0.8
    assert data["axes"]["axis3"]["p_top"] == 0.7
    assert data["axes"]["axis4"]["p_top"] == 0.6
    assert data["axes"]["axis5"]["p_top"] == 0.5
    assert data["axes"]["axis6"]["p_top"] == 0.4
    assert data["axes"]["axis7"]["p_top"] == 0.3

    print("PASS")