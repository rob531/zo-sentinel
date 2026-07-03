from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from pydantic import BaseModel
from typing import Dict
from fastapi.testclient import TestClient

router = APIRouter()

class SourceDistribution(BaseModel):
    source_distribution: Dict[str, int]

@router.get("/registry-source-distribution", response_model=SourceDistribution)
async def get_registry_source_distribution(db: Session = Depends(get_session)):
    query = db.query(MCPServerRegistry.source, MCPServerRegistry.id).group_by(MCPServerRegistry.source)
    result = query.all()
    return {"source_distribution": {source: count for source, count in result}}

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: sessionmaker(bind=engine)()

    # Create test data
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    test_data = [
        MCPServerRegistry(source="source1"),
        MCPServerRegistry(source="source2"),
        MCPServerRegistry(source="source1"),
        MCPServerRegistry(source="source3"),
        MCPServerRegistry(source="source2"),
    ]
    db.add_all(test_data)
    db.commit()

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/registry-source-distribution")
    assert response.status_code == 200
    assert response.json() == {"source_distribution": {"source1": 2, "source2": 2, "source3": 1}}
    print("PASS")