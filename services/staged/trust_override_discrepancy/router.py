from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from .logic import compute_trust_discrepancies

router = APIRouter(prefix="/api")

class Discrepancy(BaseModel):
    server_id: int
    name: str
    computed_tier: str
    override_trusted: bool
    url: str
    last_scored: str

class DiscrepancyResponse(BaseModel):
    discrepancies: List[Discrepancy]

@router.get("/verdict/override-discrepancy", response_model=DiscrepancyResponse)
def get_override_discrepancies(db: Session = Depends(get_session)):
    discrepancies = compute_trust_discrepancies(db)
    return {"discrepancies": discrepancies}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Mock data
    from app.models import McpServerRegistry, McpLlmAxisScore

    def create_test_data(session):
        # Create 5 test servers
        servers = [
            McpServerRegistry(
                id=1, name="Server 1", url="http://server1", last_scored="2023-01-01"
            ),
            McpServerRegistry(
                id=2, name="Server 2", url="http://server2", last_scored="2023-01-02"
            ),
            McpServerRegistry(
                id=3, name="Server 3", url="http://server3", last_scored="2023-01-03"
            ),
            McpServerRegistry(
                id=4, name="Server 4", url="http://server4", last_scored="2023-01-04"
            ),
            McpServerRegistry(
                id=5, name="Server 5", url="http://server5", last_scored="2023-01-05"
            ),
        ]
        session.add_all(servers)

        # Create LLM axis scores
        scores = [
            McpLlmAxisScore(
                server_id=1, p_top=0.9, p_second=0.1, p_third=0.0, axis="risk"
            ),
            McpLlmAxisScore(
                server_id=2, p_top=0.8, p_second=0.2, p_third=0.0, axis="risk"
            ),
            McpLlmAxisScore(
                server_id=3, p_top=0.3, p_second=0.4, p_third=0.3, axis="risk"
            ),
            McpLlmAxisScore(
                server_id=4, p_top=0.2, p_second=0.5, p_third=0.3, axis="risk"
            ),
            McpLlmAxisScore(
                server_id=5, p_top=0.1, p_second=0.6, p_third=0.3, axis="risk"
            ),
        ]
        session.add_all(scores)
        session.commit()

    # Mock trust_gate function
    from .logic import trust_gate

    def mock_trust_gate(url, name, data):
        if name in ["Server 1", "Server 2"]:
            return True
        return False

    from .logic import trust_gate
    trust_gate = mock_trust_gate

    # Create test client and data
    client = TestClient(app)
    with SessionLocal() as session:
        create_test_data(session)

    # Test the endpoint
    response = client.get("/api/verdict/override-discrepancy")
    assert response.status_code == 200
    data = response.json()
    assert len(data["discrepancies"]) == 2  # 2 servers with HIGH_RISK computed tier

    print("PASS")