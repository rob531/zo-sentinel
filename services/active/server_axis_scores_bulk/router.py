
from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from sqlalchemy.orm import Session
import requests
import json

router = APIRouter()

@router.get("/server_axis_scores")
async def get_server_axis_scores(
    session: Session = Depends(get_session)
):
    """Endpoint to retrieve server axis scores"""
    try:
        # Query application tables via SQLAlchemy
        servers = session.query(McpServerRegistry).all()

        # For each server, get its axis scores from pipeline tables
        results = []
        for server in servers:
            # Query mesh tables via write_service
            response = requests.post(
                "http://127.0.0.1:8772/query",
                json={
                    "sql": """
                    SELECT * FROM mcp_llm_axis_scores
                    WHERE server_id = :server_id
                    """,
                    "params": [server.id]
                }
            ).json()

            if response.get('data'):
                server_data = server.__dict__
                server_data['axis_scores'] = response['data']
                results.append(server_data)

        return {"data": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/server_axis_scores/dispute")
async def create_score_dispute(
    score_data: dict,
    session: Session = Depends(get_session)
):
    """Endpoint to create a score dispute"""
    try:
        # Create dispute record in application tables
        dispute = McpScoreDispute(
            server_id=score_data['server_id'],
            axis=score_data['axis'],
            disputed_score=score_data['score'],
            reason=score_data.get('reason', ''),
            created_by=score_data.get('created_by', 'system')
        )
        session.add(dispute)
        session.commit()
        return {"status": "success", "dispute_id": dispute.id}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Self-test block
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Setup test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Mock dependency
    class MockSession:
        def query(self, model):
            class MockQuery:
                def all(self):
                    if model.__name__ == "McpServerRegistry":
                        return [MockServer()]
                    return []
            return MockQuery()

    class MockServer:
        id = 1
        name = "test-server"
        __dict__ = {"id": 1, "name": "test-server"}

    def mock_get_session():
        return MockSession()

    # Override dependency
    test_app.dependency_overrides[get_session] = mock_get_session

    # Run tests
    client = TestClient(test_app)

    # Test get_server_axis_scores
    response = client.get("/server_axis_scores")
    assert response.status_code == 200

    # Test create_score_dispute
    response = client.post(
        "/server_axis_scores/dispute",
        json={
            "server_id": 1,
            "axis": "performance",
            "score": 85,
            "reason": "Test dispute",
            "created_by": "test-user"
        }
    )
    assert response.status_code == 200

    print("Self-tests passed")
