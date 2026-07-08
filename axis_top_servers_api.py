from fastapi import FastAPI, APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

router = APIRouter()

class ServerAxisScore(BaseModel):
    name: str
    url: str
    risk_tier: Optional[str]
    last_assessed: Optional[str]
    score: float

@router.get("/servers/top-by-axis", response_model=List[ServerAxisScore])
def get_top_servers_by_axis(
    axis_name: str = Query("overall_risk"),
    limit: int = Query(10),
    db: Session = Depends(get_session)
):
    # Join McpServerRegistry and McpLlmAxisScores on server_id
    # Order by score descending and limit results
    results = (
        db.query(
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpLlmAxisScores.score
        )
        .join(McpLlmAxisScores, McpServerRegistry.server_id == McpLlmAxisScores.server_id)
        .filter(McpLlmAxisScores.axis_name == axis_name)
        .order_by(desc(McpLlmAxisScores.score))
        .limit(limit)
        .all()
    )
    
    return [
        ServerAxisScore(
            name=r.name, 
            url=r.url, 
            risk_tier=r.risk_tier, 
            last_assessed=str(r.last_assessed) if r.last_assessed else None, 
            score=r.score
        ) for r in results
    ]

# --- Self-Test ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # Seed data
    db = TestingSessionLocal()
    servers = [
        McpServerRegistry(server_id="s1", name="Server 1", url="http://s1", risk_tier="High", last_assessed="2023-01-01"),
        McpServerRegistry(server_id="s2", name="Server 2", url="http://s2", risk_tier="Low", last_assessed="2023-01-01"),
        McpServerRegistry(server_id="s3", name="Server 3", url="http://s3", risk_tier="Med", last_assessed="2023-01-01"),
        McpServerRegistry(server_id="s4", name="Server 4", url="http://s4", risk_tier="High", last_assessed="2023-01-01"),
        McpServerRegistry(server_id="s5", name="Server 5", url="http://s5", risk_tier="Low", last_assessed="2023-01-01"),
        McpServerRegistry(server_id="s6", name="Server 6", url="http://s6", risk_tier="Med", last_assessed="2023-01-01"),
    ]
    db.add_all(servers)
    
    scores = [
        McpLlmAxisScores(server_id="s1", axis_name="auth_strength", score=10.0),
        McpLlmAxisScores(server_id="s2", axis_name="auth_strength", score=90.0),
        McpLlmAxisScores(server_id="s3", axis_name="auth_strength", score=50.0),
        McpLlmAxisScores(server_id="s4", axis_name="auth_strength", score=80.0),
        McpLlmAxisScores(server_id="s5", axis_name="auth_strength", score=70.0),
        McpLlmAxisScores(server_id="s6", axis_name="auth_strength", score=60.0),
    ]
    db.add_all(scores)
    db.commit()
    db.close()

    # Test: Top 5 by auth_strength
    response = client.get("/servers/top-by-axis?axis_name=auth_strength&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    # Verify sorting (descending score)
    scores_returned = [item["score"] for item in data]
    assert scores_returned == sorted(scores_returned, reverse=True)
    # Verify top score is 90.0 (Server 2)
    assert data[0]["name"] == "Server 2"
    
    print("PASS")