from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from pydantic import BaseModel
from typing import Dict

router = APIRouter()

class SourceDistribution(BaseModel):
    distribution: Dict[str, int]

@router.get("/registry/source-distribution", response_model=SourceDistribution)
def get_source_distribution(db: Session = Depends(get_session)):
    query = db.query(MCPServerRegistry.source, MCPServerRegistry.id).all()
    distribution = {}
    for source, _ in query:
        distribution[source] = distribution.get(source, 0) + 1
    return {"distribution": distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Seed test data
    test_data = [
        MCPServerRegistry(source="source1"),
        MCPServerRegistry(source="source2"),
        MCPServerRegistry(source="source1"),
        MCPServerRegistry(source="source3"),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override the dependency
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Create and test the app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/registry/source-distribution")
    assert response.status_code == 200
    assert response.json()["distribution"] == {"source1": 2, "source2": 1, "source3": 1}
    print("PASS")