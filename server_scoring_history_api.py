from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    adapter_sha256: str
    model_version: str
    decision_rule_version: str

class ScoringSession(BaseModel):
    scored_at: datetime
    axes: List[AxisScore]

class ScoringHistoryResponse(BaseModel):
    sessions: List[ScoringSession]

def get_scoring_history(server_id: str, axis: Optional[str] = None, limit: Optional[int] = None) -> List[ScoringSession]:
    query = """
    SELECT * FROM mcp_llm_axis_scores
    WHERE server_id = :server_id
    ORDER BY scored_at DESC
    """
    params = {"server_id": server_id}

    if axis:
        query += " AND axis_name = :axis"
        params["axis"] = axis

    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params}
    )
    response.raise_for_status()

    rows = response.json()["rows"]
    sessions = {}

    for row in rows:
        scored_at = row["scored_at"]
        if scored_at not in sessions:
            sessions[scored_at] = {
                "scored_at": scored_at,
                "axes": []
            }

        sessions[scored_at]["axes"].append(AxisScore(
            axis_name=row["axis_name"],
            label=row["label"],
            label_index=row["label_index"],
            probs=row["probs"],
            p_top=row["p_top"],
            p_critical=row["p_critical"],
            p_danger=row["p_danger"],
            escalated=row["escalated"],
            adapter_sha256=row["adapter_sha256"],
            model_version=row["model_version"],
            decision_rule_version=row["decision_rule_version"]
        ))

    return list(sessions.values())

@router.get("/servers/{server_id}/scoring-history", response_model=ScoringHistoryResponse)
async def scoring_history(
    server_id: str,
    axis: Optional[str] = Query(None),
    limit: Optional[int] = Query(None)
):
    try:
        sessions = get_scoring_history(server_id, axis, limit)
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(test_app)

    test_data = [
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-01T00:00:00",
            "axis_name": "overall_risk",
            "label": "low",
            "label_index": 0,
            "probs": {"low": 0.8, "medium": 0.1, "high": 0.1},
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.1,
            "escalated": False,
            "adapter_sha256": "a1b2c3",
            "model_version": "1.0",
            "decision_rule_version": "1.0"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-01T00:00:00",
            "axis_name": "auth_strength",
            "label": "medium",
            "label_index": 1,
            "probs": {"low": 0.1, "medium": 0.7, "high": 0.2},
            "p_top": 0.7,
            "p_critical": 0.2,
            "p_danger": 0.2,
            "escalated": False,
            "adapter_sha256": "a1b2c3",
            "model_version": "1.0",
            "decision_rule_version": "1.0"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-02T00:00:00",
            "axis_name": "overall_risk",
            "label": "medium",
            "label_index": 1,
            "probs": {"low": 0.2, "medium": 0.6, "high": 0.2},
            "p_top": 0.6,
            "p_critical": 0.2,
            "p_danger": 0.2,
            "escalated": True,
            "adapter_sha256": "d4e5f6",
            "model_version": "1.1",
            "decision_rule_version": "1.1"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-02T00:00:00",
            "axis_name": "capability_breadth",
            "label": "high",
            "label_index": 2,
            "probs": {"low": 0.1, "medium": 0.3, "high": 0.6},
            "p_top": 0.6,
            "p_critical": 0.3,
            "p_danger": 0.3,
            "escalated": True,
            "adapter_sha256": "d4e5f6",
            "model_version": "1.1",
            "decision_rule_version": "1.1"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-03T00:00:00",
            "axis_name": "overall_risk",
            "label": "high",
            "label_index": 2,
            "probs": {"low": 0.1, "medium": 0.2, "high": 0.7},
            "p_top": 0.7,
            "p_critical": 0.2,
            "p_danger": 0.2,
            "escalated": False,
            "adapter_sha256": "g7h8i9",
            "model_version": "1.2",
            "decision_rule_version": "1.2"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-03T00:00:00",
            "axis_name": "data_sensitivity",
            "label": "low",
            "label_index": 0,
            "probs": {"low": 0.7, "medium": 0.2, "high": 0.1},
            "p_top": 0.7,
            "p_critical": 0.2,
            "p_danger": 0.2,
            "escalated": False,
            "adapter_sha256": "g7h8i9",
            "model_version": "1.2",
            "decision_rule_version": "1.2"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-03T00:00:00",
            "axis_name": "network_egress",
            "label": "medium",
            "label_index": 1,
            "probs": {"low": 0.3, "medium": 0.5, "high": 0.2},
            "p_top": 0.5,
            "p_critical": 0.2,
            "p_danger": 0.2,
            "escalated": False,
            "adapter_sha256": "g7h8i9",
            "model_version": "1.2",
            "decision_rule_version": "1.2"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-03T00:00:00",
            "axis_name": "maintainer_trust",
            "label": "high",
            "label_index": 2,
            "probs": {"low": 0.1, "medium": 0.3, "high": 0.6},
            "p_top": 0.6,
            "p_critical": 0.3,
            "p_danger": 0.3,
            "escalated": False,
            "adapter_sha256": "g7h8i9",
            "model_version": "1.2",
            "decision_rule_version": "1.2"
        },
        {
            "server_id": "test-server-1",
            "scored_at": "2023-01-03T00:00:00",
            "axis_name": "exploit_surface",
            "label": "low",
            "label_index": 0,
            "probs": {"low": 0.6, "medium": 0.3, "high": 0.1},
            "p_top": 0.6,
            "p_critical": 0.3,
            "p_danger": 0.3,
            "escalated": False,
            "adapter_sha256": "g7h8i9",
            "model_version": "1.2",
            "decision_rule_version": "1.2"
        },
    ]

    with TestSession() as session:
        for data in test_data:
            session.add(MCPLLMAxisScore(**data))
        session.commit()

    response = test_client.get("/servers/test-server-1/scoring-history")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 3
    assert len(data["sessions"][0]["axes"]) == 7
    assert len(data["sessions"][1]["axes"]) == 2
    assert len(data["sessions"][2]["axes"]) == 1

    response = test_client.get("/servers/test-server-1/scoring-history?axis=overall_risk")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 3
    assert len(data["sessions"][0]["axes"]) == 1
    assert len(data["sessions"][1]["axes"]) == 1
    assert len(data["sessions"][2]["axes"]) == 1

    print("PASS")