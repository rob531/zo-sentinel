from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

class AxisScoreSummary(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: str
    model_version: str
    decision_rule_version: str

class ServerAxisScoresSummary(BaseModel):
    server_id: str
    axes: List[AxisScoreSummary]
    count: int
    fetched_at: str

@router.get("/servers/{server_id}/axis-scores-summary", response_model=ServerAxisScoresSummary)
async def get_server_axis_scores_summary(server_id: str):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": f"SELECT * FROM mcp_llm_axis_scores WHERE server_id = '{server_id}'"
            }
        )
        response.raise_for_status()
        data = response.json()

        axes = []
        for row in data:
            axes.append(AxisScoreSummary(
                axis_name=row["axis_name"],
                label=row["label"],
                label_index=row["label_index"],
                p_top=row["p_top"],
                p_critical=row["p_critical"],
                p_danger=row["p_danger"],
                escalated=row["escalated"],
                scored_at=row["scored_at"],
                model_version=row["model_version"],
                decision_rule_version=row["decision_rule_version"]
            ))

        return ServerAxisScoresSummary(
            server_id=server_id,
            axes=axes,
            count=len(axes),
            fetched_at=datetime.utcnow().isoformat()
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    import sqlite3
    from contextlib import contextmanager
    from app.models import Base

    @contextmanager
    def mock_db_session():
        conn = sqlite3.connect(":memory:")
        Base.metadata.create_all(conn)
        session = get_session(override=True)
        try:
            yield session
        finally:
            session.close()
            conn.close()

    def mock_requests_post(*args, **kwargs):
        if "mcp_llm_axis_scores" in kwargs["json"]["query"]:
            return type('MockResponse', (), {
                'json': lambda: [
                    {
                        "server_id": "test_server_1",
                        "axis_name": f"axis_{i}",
                        "label": f"label_{i}",
                        "label_index": i,
                        "p_top": 0.1 * i,
                        "p_critical": 0.2 * i,
                        "p_danger": 0.3 * i,
                        "escalated": i % 2 == 0,
                        "scored_at": "2023-01-01T00:00:00",
                        "model_version": "1.0",
                        "decision_rule_version": "1.0"
                    } for i in range(14)
                ],
                'status_code': 200
            })()
        raise Exception("Unexpected query")

    app.dependency_overrides[get_session] = mock_db_session
    requests.post = mock_requests_post

    client = TestClient(app)
    response = client.get("/servers/test_server_1/axis-scores-summary")

    assert response.status_code == 200
    assert response.json()["server_id"] == "test_server_1"
    assert response.json()["count"] == 14
    assert len(response.json()["axes"]) > 0
    assert "p_top" in response.json()["axes"][0]

    print("PASS")