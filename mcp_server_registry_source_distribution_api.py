from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict
from .database import get_db

router = APIRouter()

class SourceDistributionResponse(BaseModel):
    source_distribution: Dict[str, int]

@router.get("/mcp_server_registry/source_distribution", response_model=SourceDistributionResponse)
def get_source_distribution(db: Session = Depends(get_db)):
    """
    Query the mcp_server_registry table, group entries by registry_source,
    and return a count for each source.
    """
    query = """
        SELECT registry_source, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY registry_source
    """
    result = db.execute(query)
    source_distribution = {row.registry_source: row.count for row in result}

    return {"source_distribution": source_distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from .database import Base, engine, SessionLocal
    from .models import MCPServerRegistry

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed test data
    with SessionLocal() as db:
        test_data = [
            MCPServerRegistry(registry_source="source1"),
            MCPServerRegistry(registry_source="source1"),
            MCPServerRegistry(registry_source="source2"),
            MCPServerRegistry(registry_source="source3"),
            MCPServerRegistry(registry_source="source3"),
            MCPServerRegistry(registry_source="source3"),
        ]
        db.add_all(test_data)
        db.commit()

    # Create test client
    from .main import app
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/mcp_server_registry/source_distribution")
    assert response.status_code == 200
    assert response.json() == {
        "source_distribution": {
            "source1": 2,
            "source2": 1,
            "source3": 3
        }
    }

    print("PASS")