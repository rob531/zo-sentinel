from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPDefinitionHistoryPipelineStatus
from sqlalchemy.orm import Session

router = APIRouter()

class PipelineStatusData(BaseModel):
    status: str
    last_updated: Optional[datetime] = None

class PipelineStatusResponse(BaseModel):
    status: str
    data: PipelineStatusData
    last_updated: Optional[datetime] = None

@router.get("/mcp/risk-tier-definition-history/pipeline-status/dashboard", response_model=PipelineStatusResponse)
async def get_pipeline_status_dashboard(db: Session = Depends(get_session)):
    pipeline_status = db.query(MCPDefinitionHistoryPipelineStatus).first()

    if not pipeline_status:
        return {
            "status": "error",
            "data": {"status": "No pipeline status data found", "last_updated": None},
            "last_updated": None
        }

    return {
        "status": "success",
        "data": {
            "status": pipeline_status.status,
            "last_updated": pipeline_status.last_updated
        },
        "last_updated": pipeline_status.last_updated
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPDefinitionHistoryPipelineStatus
    from sqlalchemy.orm import sessionmaker

    # Override the database session for testing
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_db = TestSessionLocal()

    # Create test data
    test_data = MCPDefinitionHistoryPipelineStatus(
        status="completed",
        last_updated=datetime.now()
    )
    test_db.add(test_data)
    test_db.commit()

    # Create the FastAPI app and override the dependency
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: test_db

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp/risk-tier-definition-history/pipeline-status/dashboard")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["data"]["status"] == "completed"
    assert response.json()["data"]["last_updated"] is not None

    # Clean up
    test_db.query(MCPDefinitionHistoryPipelineStatus).delete()
    test_db.commit()
    test_db.close()

    print("PASS")