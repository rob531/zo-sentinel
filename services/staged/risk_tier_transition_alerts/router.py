from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from .logic import get_risk_tier_transitions
from pydantic import BaseModel

router = APIRouter(prefix="/api")

class ServerTransition(BaseModel):
    server_id: int
    name: str
    old_tier: str
    new_tier: str
    changed_at: str

class RiskTierTransitionsResponse(BaseModel):
    servers: list[ServerTransition]

@router.get("/risk/transitions", response_model=RiskTierTransitionsResponse)
def get_transitions(session: Session = Depends(get_session)):
    return get_risk_tier_transitions(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app = FastAPI()
    app.include_router(router)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Seed servers
        server1 = McpServerRegistry(
            id=1,
            name="test-server-1",
            risk_tier="low",
            created_at="2023-01-01T00:00:00",
            updated_at="2023-01-01T00:00:00"
        )
        server2 = McpServerRegistry(
            id=2,
            name="test-server-2",
            risk_tier="medium",
            created_at="2023-01-01T00:00:00",
            updated_at="2023-01-01T00:00:00"
        )
        server3 = McpServerRegistry(
            id=3,
            name="test-server-3",
            risk_tier="high",
            created_at="2023-01-01T00:00:00",
            updated_at="2023-01-01T00:00:00"
        )
        session.add_all([server1, server2, server3])

        # Seed scores with tier changes
        score1 = McpLlmAxisScore(
            server_id=1,
            risk_tier="medium",
            created_at="2023-01-02T00:00:00",
            updated_at="2023-01-02T00:00:00"
        )
        score2 = McpLlmAxisScore(
            server_id=2,
            risk_tier="high",
            created_at="2023-01-02T00:00:00",
            updated_at="2023-01-02T00:00:00"
        )
        score3 = McpLlmAxisScore(
            server_id=3,
            risk_tier="low",
            created_at="2023-01-02T00:00:00",
            updated_at="2023-01-02T00:00:00"
        )
        session.add_all([score1, score2, score3])
        session.commit()

    # Test
    client = TestClient(app)
    response = client.get("/api/risk/transitions")
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) == 3
    assert any(server["server_id"] == 1 for server in data["servers"])
    print("PASS")