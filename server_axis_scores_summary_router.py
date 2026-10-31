from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScoreSummary(BaseModel):
    axis_name: str
    # nullable columns in mcp_llm_axis_scores
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    scored_at: Optional[str] = None
    model_version: Optional[str] = None
    decision_rule_version: Optional[str] = None

class ServerAxisScoresSummary(BaseModel):
    server_id: str
    axes: List[AxisScoreSummary]
    count: int
    fetched_at: str

@router.get("/servers/{server_id}/axis-scores-summary", response_model=ServerAxisScoresSummary)
async def get_server_axis_scores_summary(server_id: str, db: Session = Depends(get_session)):
    # DB-direct (was an HTTP hop + f-string SQL to the :8772 write-service that isn't
    # running in prod -> 500, zo-sentinel #12368). Parameterized ORM, no injection.
    rows = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    axes = [
        AxisScoreSummary(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated) if r.escalated is not None else None,
            scored_at=r.scored_at.isoformat() if r.scored_at else None,
            model_version=r.model_version,
            decision_rule_version=r.decision_rule_version,
        )
        for r in rows
    ]
    return ServerAxisScoresSummary(
        server_id=server_id,
        axes=axes,
        count=len(axes),
        fetched_at=datetime.utcnow().isoformat(),
    )

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