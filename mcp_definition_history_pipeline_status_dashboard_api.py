from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import Dict, List
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPDefinitionHistory

router = APIRouter()

class PipelineStatusResponse(BaseModel):
    status_counts: Dict[str, int]
    top_servers_per_status: Dict[str, List[str]]

@router.get("/dashboard/definition-history-pipeline-status", response_model=PipelineStatusResponse)
def get_definition_history_pipeline_status(db: Session = Depends(get_session)):
    # Get status counts
    status_counts = db.query(
        MCPDefinitionHistory.status,
        func.count(MCPDefinitionHistory.status).label('count')
    ).group_by(
        MCPDefinitionHistory.status
    ).all()

    status_counts_dict = {status: count for status, count in status_counts}

    # Get top 5 servers per status
    top_servers_query = db.query(
        MCPDefinitionHistory.status,
        MCPDefinitionHistory.server_id,
        func.count(MCPDefinitionHistory.server_id).label('server_count')
    ).group_by(
        MCPDefinitionHistory.status,
        MCPDefinitionHistory.server_id
    ).order_by(
        MCPDefinitionHistory.status,
        desc('server_count')
    ).all()

    top_servers_per_status = {}
    for status, server_id, _ in top_servers_query:
        if status not in top_servers_per_status:
            top_servers_per_status[status] = []
        if len(top_servers_per_status[status]) < 5:
            top_servers_per_status[status].append(server_id)

    return {
        "status_counts": status_counts_dict,
        "top_servers_per_status": top_servers_per_status
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Seed test data
    with TestSessionLocal() as session:
        test_data = [
            {"status": "pending", "server_id": "server1"},
            {"status": "pending", "server_id": "server2"},
            {"status": "pending", "server_id": "server3"},
            {"status": "pending", "server_id": "server4"},
            {"status": "pending", "server_id": "server5"},
            {"status": "pending", "server_id": "server6"},
            {"status": "processing", "server_id": "server1"},
            {"status": "processing", "server_id": "server2"},
            {"status": "processing", "server_id": "server3"},
            {"status": "completed", "server_id": "server1"},
            {"status": "completed", "server_id": "server2"},
            {"status": "failed", "server_id": "server1"},
        ]

        for data in test_data:
            session.add(MCPDefinitionHistory(**data))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/dashboard/definition-history-pipeline-status")
    assert response.status_code == 200
    assert response.json()["status_counts"] == {
        "pending": 6,
        "processing": 3,
        "completed": 2,
        "failed": 1
    }
    assert response.json()["top_servers_per_status"] == {
        "pending": ["server6", "server5", "server4", "server3", "server2"],
        "processing": ["server3", "server2", "server1"],
        "completed": ["server2", "server1"],
        "failed": ["server1"]
    }

    print("PASS")