from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Dict

from app.db import get_db
from app.models import MCPDefinitionHistory

router = APIRouter()

class PipelineStatusResponse(BaseModel):
    status_counts: Dict[str, int]
    override_tier: str

@router.get("/definition-history-pipeline-status", response_model=PipelineStatusResponse)
async def get_definition_history_pipeline_status(db: Session = Depends(get_db)):
    # Query pipeline status counts
    status_counts = db.execute(
        select(
            MCPDefinitionHistory.pipeline_status,
            func.count(MCPDefinitionHistory.pipeline_status).label("count")
        ).group_by(MCPDefinitionHistory.pipeline_status)
    ).fetchall()

    # Convert to dict
    status_dict = {status: count for status, count in status_counts}

    # Determine override tier (CRITICAL forces tier to CRITICAL)
    override_tier = "CRITICAL" if status_dict.get("CRITICAL", 0) > 0 else "NORMAL"

    return {
        "status_counts": status_dict,
        "override_tier": override_tier
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine

    # Create in-memory database and tables
    Base.metadata.create_all(bind=engine)

    # Seed test data
    from app.models import MCPDefinitionHistory
    from app.db import SessionLocal
    db = SessionLocal()
    test_data = [
        MCPDefinitionHistory(pipeline_status="PASSED"),
        MCPDefinitionHistory(pipeline_status="FAILED"),
        MCPDefinitionHistory(pipeline_status="CRITICAL"),
        MCPDefinitionHistory(pipeline_status="PASSED"),
        MCPDefinitionHistory(pipeline_status="FAILED"),
    ]
    db.add_all(test_data)
    db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/definition-history-pipeline-status")
    assert response.status_code == 200
    assert response.json()["status_counts"] == {"PASSED": 2, "FAILED": 2, "CRITICAL": 1}
    assert response.json()["override_tier"] == "CRITICAL"

    print("PASS")