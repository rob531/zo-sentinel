from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScoreHistoryResponse(BaseModel):
    axis_name: str
    p_top: float
    p_critical: float
    scored_at: datetime
    model_version: str
    decision_rule_version: str

@router.get("/servers/{server_id}/axis-scores/history", response_model=List[AxisScoreHistoryResponse])
async def get_axis_score_history(
    server_id: int,
    axis_name: Optional[str] = None,
    limit: int = Query(20, le=200),
    order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_session)
):
    query = db.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id)

    if axis_name:
        query = query.filter(MCPLLMAxisScore.axis_name == axis_name)

    if order == "desc":
        query = query.order_by(MCPLLMAxisScore.scored_at.desc())
    else:
        query = query.order_by(MCPLLMAxisScore.scored_at.asc())

    results = query.limit(limit).all()

    return [
        AxisScoreHistoryResponse(
            axis_name=score.axis_name,
            p_top=score.p_top,
            p_critical=score.p_critical,
            scored_at=score.scored_at,
            model_version=score.model_version,
            decision_rule_version=score.decision_rule_version
        )
        for score in results
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScore
    from datetime import datetime, timedelta
    from app.dependency_overrides import override_get_session

    # Setup test database
    Base.metadata.create_all(bind=engine)
    override_get_session()

    from main import app
    client = TestClient(app)

    # Seed test data
    test_server_id = 1
    test_axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust",
        "exploit_surface"
    ]

    test_session = override_get_session()
    for i, axis in enumerate(test_axes):
        test_session.add(MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name=axis,
            p_top=0.1 * (i + 1),
            p_critical=0.05 * (i + 1),
            scored_at=datetime.now() - timedelta(days=i),
            model_version="test_model",
            decision_rule_version="test_rule"
        ))
    test_session.commit()

    # Test endpoint
    response = client.get(f"/servers/{test_server_id}/axis-scores/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert all("axis_name" in item for item in data)
    assert all("p_top" in item for item in data)
    assert all("p_critical" in item for item in data)
    assert all("scored_at" in item for item in data)

    # Test axis filter
    filter_axis = "auth_strength"
    filtered_response = client.get(f"/servers/{test_server_id}/axis-scores/history?axis_name={filter_axis}")
    assert filtered_response.status_code == 200
    filtered_data = filtered_response.json()
    assert all(item["axis_name"] == filter_axis for item in filtered_data)

    print("PASS")