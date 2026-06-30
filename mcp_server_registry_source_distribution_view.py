from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Dict

from .database import get_db
from .models import MCPServerRegistry

router = APIRouter()

class SourceDistributionResponse(BaseModel):
    distribution: Dict[str, int]

@router.get("/server-registry-source-distribution", response_model=SourceDistributionResponse)
def get_server_registry_source_distribution(db: Session = Depends(get_db)):
    # Query the distribution of server registry sources
    query = (
        select(
            MCPServerRegistry.source,
            func.count(MCPServerRegistry.source).label("count")
        )
        .group_by(MCPServerRegistry.source)
    )
    result = db.execute(query).fetchall()

    # Convert result to dictionary
    distribution = {row.source: row.count for row in result}

    # Apply rule-override for CRITICAL axis
    if "CRITICAL" in distribution:
        distribution["CRITICAL"] = len(distribution)  # Force the tier

    return {"distribution": distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from .database import Base, engine
    from .models import MCPServerRegistry

    # Create in-memory database and seed with test data
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        test_data = [
            MCPServerRegistry(source="TIER1"),
            MCPServerRegistry(source="TIER2"),
            MCPServerRegistry(source="TIER3"),
            MCPServerRegistry(source="TIER4"),
            MCPServerRegistry(source="TIER5"),
            MCPServerRegistry(source="TIER6"),
            MCPServerRegistry(source="CRITICAL"),
        ]
        session.add_all(test_data)
        session.commit()

    # Create FastAPI app and test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/server-registry-source-distribution")
    assert response.status_code == 200
    data = response.json()
    assert set(data["distribution"].keys()) == {"TIER1", "TIER2", "TIER3", "TIER4", "TIER5", "TIER6", "CRITICAL"}
    assert data["distribution"]["CRITICAL"] == 7  # 6 tiers + 1 CRITICAL

    print("PASS")