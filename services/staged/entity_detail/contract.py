from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()

class SignalScore(BaseModel):
    label: str
    p_top: float

class EntityDetail(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    signals: dict[str, SignalScore]

@router.get("/api/entities/{server_id}", response_model=EntityDetail)
async def get_entity_detail(server_id: str, session: Session = Depends(get_session)) -> EntityDetail:
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    signals = {}
    for score in scores:
        signals[score.axis_name] = SignalScore(label=score.label, p_top=score.p_top)

    return EntityDetail(
        server_id=server.server_id,
        name=server.name,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        signals=signals
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server = McpServerRegistry(
        server_id="test-server-1",
        name="Test Server 1",
        risk_tier="high",
        verdict="malicious"
    )
    test_session.add(test_server)
    test_session.commit()

    test_scores = [
        McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="axis1",
            label="label1",
            p_top=0.95
        ),
        McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="axis2",
            label="label2",
            p_top=0.85
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Create FastAPI app and test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/entities/test-server-1")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": "test-server-1",
        "name": "Test Server 1",
        "risk_tier": "high",
        "verdict": "malicious",
        "signals": {
            "axis1": {"label": "label1", "p_top": 0.95},
            "axis2": {"label": "label2", "p_top": 0.85}
        }
    }

    print("PASS")