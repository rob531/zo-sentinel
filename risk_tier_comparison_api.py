from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session

router = APIRouter()

class RiskTierComparison(BaseModel):
    server_id: int
    server_name: str
    risk_tier: str
    overall_risk: float

class RiskTierComparisonResponse(BaseModel):
    comparisons: List[RiskTierComparison]

@router.get("/risk_tier/comparison", response_model=RiskTierComparisonResponse)
async def get_risk_tier_comparison(db: Session = Depends(get_session)):
    servers = db.query(MCPServerRegistry).all()
    comparisons = []

    for server in servers:
        comparison = RiskTierComparison(
            server_id=server.id,
            server_name=server.name,
            risk_tier=server.risk_tier,
            overall_risk=server.overall_risk
        )
        comparisons.append(comparison)

    return RiskTierComparisonResponse(comparisons=comparisons)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    # FU-369: `app.dependency_overrides` is not a module in this repo, so the import
    # that stood here raised ModuleNotFoundError the moment this block ran. The
    # override is defined locally instead, per the pattern in
    # services/active/cadence_job_sla_report/contract.py.
    from sqlalchemy import create_engine as _fu369_create_engine
    from sqlalchemy.orm import sessionmaker as _fu369_sessionmaker

    _FU369Session = _fu369_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_fu369_create_engine("sqlite:///:memory:"),
    )


    def _fu369_session_override(session_factory=None):
        """Test session override covering every call shape used in this repo.

        Called with a sessionmaker it returns a dependency callable bound to that
        factory; called with nothing it returns a Session, which is what a FastAPI
        dependency override needs AND what `with ... as session:` needs, because
        Session implements the context-manager protocol itself.
        """
        if session_factory is not None:
            return lambda: session_factory()
        return _FU369Session()

    # Create a test database
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = _fu369_session_override

    # Create a test client
    client = TestClient(app)

    # Seed the database with test data
    test_server1 = MCPServerRegistry(
        name="Test Server 1",
        risk_tier="High",
        overall_risk=0.8
    )
    test_server2 = MCPServerRegistry(
        name="Test Server 2",
        risk_tier="Medium",
        overall_risk=0.5
    )

    with _fu369_session_override() as session:
        session.add(test_server1)
        session.add(test_server2)
        session.commit()

    # Test the endpoint
    response = client.get("/risk_tier/comparison")
    assert response.status_code == 200
    assert len(response.json()["comparisons"]) == 2
    assert response.json()["comparisons"][0]["server_name"] == "Test Server 1"
    assert response.json()["comparisons"][1]["server_name"] == "Test Server 2"

    print("PASS")