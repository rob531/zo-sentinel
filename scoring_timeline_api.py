from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpLlmAxisScores
from sqlalchemy.orm import Session
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class AxisSnapshot(BaseModel):
    overall_risk: dict
    auth_strength: dict
    capability_breadth: dict
    data_sensitivity: dict
    network_egress: dict
    maintainer_trust: dict
    exploit_surface: dict

class ScoringTimelineResponse(BaseModel):
    snapshots: List[AxisSnapshot]

def get_write_service_query(url: str, query: str, params: dict) -> dict:
    response = requests.post(url, json={"query": query, "params": params})
    response.raise_for_status()
    return response.json()

@router.get("/servers/{server_id}/scoring-timeline", response_model=ScoringTimelineResponse)
async def get_scoring_timeline(
    server_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_session),
):
    query = """
    SELECT scored_at, axis_name, p_top, label
    FROM mcp_llm_axis_scores
    WHERE server_id = :server_id
    """

    params = {"server_id": server_id}

    if start_date:
        query += " AND scored_at >= :start_date"
        params["start_date"] = start_date
    if end_date:
        query += " AND scored_at <= :end_date"
        params["end_date"] = end_date

    query += " ORDER BY scored_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    try:
        result = get_write_service_query("http://127.0.0.1:8772/query", query, params)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

    snapshots = {}
    for row in result:
        scored_at = row["scored_at"]
        if scored_at not in snapshots:
            snapshots[scored_at] = {}
        snapshots[scored_at][row["axis_name"]] = {"p_top": row["p_top"], "label": row["label"]}

    snapshots_list = [AxisSnapshot(**snapshot) for snapshot in snapshots.values()]
    return {"snapshots": snapshots_list}

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    test_data = [
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "overall_risk", "p_top": 0.8, "label": "High"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "auth_strength", "p_top": 0.6, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "capability_breadth", "p_top": 0.7, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "data_sensitivity", "p_top": 0.5, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "network_egress", "p_top": 0.9, "label": "High"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "maintainer_trust", "p_top": 0.4, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-01", "axis_name": "exploit_surface", "p_top": 0.3, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "overall_risk", "p_top": 0.7, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "auth_strength", "p_top": 0.5, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "capability_breadth", "p_top": 0.6, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "data_sensitivity", "p_top": 0.4, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "network_egress", "p_top": 0.8, "label": "High"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "maintainer_trust", "p_top": 0.5, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-02", "axis_name": "exploit_surface", "p_top": 0.4, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "overall_risk", "p_top": 0.6, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "auth_strength", "p_top": 0.7, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "capability_breadth", "p_top": 0.5, "label": "Low"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "data_sensitivity", "p_top": 0.6, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "network_egress", "p_top": 0.7, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "maintainer_trust", "p_top": 0.6, "label": "Medium"},
        {"server_id": "test-server-1", "scored_at": "2023-01-03", "axis_name": "exploit_surface", "p_top": 0.5, "label": "Low"},
    ]

    def mock_get_write_service_query(url: str, query: str, params: dict) -> dict:
        filtered_data = [row for row in test_data if row["server_id"] == params["server_id"]]
        if "start_date" in params:
            filtered_data = [row for row in filtered_data if row["scored_at"] >= params["start_date"]]
        if "end_date" in params:
            filtered_data = [row for row in filtered_data if row["scored_at"] <= params["end_date"]]
        filtered_data = filtered_data[params["offset"]:params["offset"] + params["limit"]]
        return filtered_data

    app.dependency_overrides[get_write_service_query] = mock_get_write_service_query

    client = TestClient(app)
    response = client.get("/servers/test-server-1/scoring-timeline")
    assert response.status_code == 200
    data = response.json()
    assert len(data["snapshots"]) >= 2
    for snapshot in data["snapshots"]:
        assert set(snapshot.keys()) == {
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        }
    print("PASS")