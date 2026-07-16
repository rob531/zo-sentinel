from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores
import requests
from pydantic import BaseModel

router = APIRouter()

class TimelineEntry(BaseModel):
    timestamp: datetime
    risk_tier: str
    overall_risk: float
    axis_scores: Dict[str, float]

def query_write_service(sql: str, params: dict = None) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params or {}}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error querying write_service: {str(e)}")

@router.get("/timeline/{server_id}", response_model=List[TimelineEntry])
async def get_scoring_timeline(
    server_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    session: Session = Depends(get_session)
) -> List[Dict[str, Any]]:
    # Get all axis scores for the server within the time range
    axis_scores_query = """
        SELECT server_id, axis_name, p_top, scored_at
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
    """
    params = {"server_id": server_id}
    if start:
        axis_scores_query += " AND scored_at >= :start"
        params["start"] = start
    if end:
        axis_scores_query += " AND scored_at <= :end"
        params["end"] = end
    axis_scores_query += " ORDER BY scored_at ASC"

    axis_scores = query_write_service(axis_scores_query, params)

    # Get server registry entries for the server within the time range
    registry_query = """
        SELECT server_id, risk_tier, overall_risk, last_assessed
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """
    params = {"server_id": server_id}
    if start:
        registry_query += " AND last_assessed >= :start"
        params["start"] = start
    if end:
        registry_query += " AND last_assessed <= :end"
        params["end"] = end
    registry_query += " ORDER BY last_assessed ASC"

    registry_entries = query_write_service(registry_query, params)

    # Create a timeline by merging the two datasets
    timeline = []
    axis_scores_ptr = 0
    registry_ptr = 0

    while axis_scores_ptr < len(axis_scores) or registry_ptr < len(registry_entries):
        if axis_scores_ptr >= len(axis_scores):
            # Only registry entries left
            entry = registry_entries[registry_ptr]
            timeline.append({
                "timestamp": entry["last_assessed"],
                "risk_tier": entry["risk_tier"],
                "overall_risk": entry["overall_risk"],
                "axis_scores": {}
            })
            registry_ptr += 1
        elif registry_ptr >= len(registry_entries):
            # Only axis scores left
            entry = axis_scores[axis_scores_ptr]
            timeline.append({
                "timestamp": entry["scored_at"],
                "risk_tier": None,
                "overall_risk": None,
                "axis_scores": {entry["axis_name"]: entry["p_top"]}
            })
            axis_scores_ptr += 1
        else:
            # Both have entries
            axis_entry = axis_scores[axis_scores_ptr]
            registry_entry = registry_entries[registry_ptr]

            if axis_entry["scored_at"] < registry_entry["last_assessed"]:
                timeline.append({
                    "timestamp": axis_entry["scored_at"],
                    "risk_tier": None,
                    "overall_risk": None,
                    "axis_scores": {axis_entry["axis_name"]: axis_entry["p_top"]}
                })
                axis_scores_ptr += 1
            elif axis_entry["scored_at"] > registry_entry["last_assessed"]:
                timeline.append({
                    "timestamp": registry_entry["last_assessed"],
                    "risk_tier": registry_entry["risk_tier"],
                    "overall_risk": registry_entry["overall_risk"],
                    "axis_scores": {}
                })
                registry_ptr += 1
            else:
                # Same timestamp
                timeline.append({
                    "timestamp": axis_entry["scored_at"],
                    "risk_tier": registry_entry["risk_tier"],
                    "overall_risk": registry_entry["overall_risk"],
                    "axis_scores": {axis_entry["axis_name"]: axis_entry["p_top"]}
                })
                axis_scores_ptr += 1
                registry_ptr += 1

    return timeline

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Create a test app
    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the get_session dependency for testing
    async def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = override_get_session

    # Insert test data
    with TestSessionLocal() as session:
        # Insert into mcp_server_registry
        session.execute(
            "INSERT INTO mcp_server_registry (server_id, risk_tier, overall_risk, last_assessed) VALUES "
            "('test_server', 'low', 0.1, '2023-01-01 00:00:00'), "
            "('test_server', 'medium', 0.5, '2023-01-02 00:00:00'), "
            "('test_server', 'high', 0.9, '2023-01-03 00:00:00')"
        )

        # Insert into mcp_llm_axis_scores
        session.execute(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, scored_at) VALUES "
            "('test_server', 'axis1', 0.2, '2023-01-01 00:00:00'), "
            "('test_server', 'axis2', 0.3, '2023-01-01 00:00:00'), "
            "('test_server', 'axis1', 0.4, '2023-01-02 00:00:00'), "
            "('test_server', 'axis2', 0.5, '2023-01-02 00:00:00'), "
            "('test_server', 'axis1', 0.6, '2023-01-03 00:00:00'), "
            "('test_server', 'axis2', 0.7, '2023-01-03 00:00:00')"
        )
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/timeline/test_server")
    assert response.status_code == 200
    timeline = response.json()

    # Verify the timeline has the expected number of entries
    assert len(timeline) == 6

    # Verify risk tier transitions are captured
    risk_tiers = [entry["risk_tier"] for entry in timeline if entry["risk_tier"] is not None]
    assert risk_tiers == ["low", "medium", "high"]

    print("PASS")