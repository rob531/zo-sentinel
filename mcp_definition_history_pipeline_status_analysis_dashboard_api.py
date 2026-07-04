from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpDefinitionHistory
from pydantic import BaseModel
from typing import Dict

router = APIRouter()

class PipelineStatusAnalysis(BaseModel):
    status_counts: Dict[str, int]

@router.get("/definition-history-pipeline-status-analysis", response_model=PipelineStatusAnalysis)
def get_pipeline_status_analysis(db: Session = Depends(get_session)):
    status_counts = db.query(
        McpDefinitionHistory.status,
        McpDefinitionHistory.server_id
    ).group_by(
        McpDefinitionHistory.status
    ).count().all()

    result = {status: count for status, count in status_counts}
    return {"status_counts": result}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpDefinitionHistory
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_data = [
        McpDefinitionHistory(status="completed", server_id="server1"),
        McpDefinitionHistory(status="failed", server_id="server2"),
        McpDefinitionHistory(status="completed", server_id="server3"),
        McpDefinitionHistory(status="pending", server_id="server4"),
        McpDefinitionHistory(status="failed", server_id="server5"),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Create test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/definition-history-pipeline-status-analysis")
    assert response.status_code == 200
    assert response.json() == {
        "status_counts": {
            "completed": 2,
            "failed": 2,
            "pending": 1
        }
    }

    print("PASS")