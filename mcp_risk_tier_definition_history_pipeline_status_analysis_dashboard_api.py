from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime
from app.db import get_session
from app.models import MCPDefinitionHistory
from sqlalchemy import func
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

router = APIRouter()

class PipelineStatusMetrics(BaseModel):
    status_metrics: Dict[str, Dict[str, Any]]
    overall_status: str
    last_updated: datetime

def get_pipeline_status_metrics(db: Session) -> PipelineStatusMetrics:
    # Get the latest pipeline run status
    latest_run = db.query(MCPDefinitionHistory).order_by(MCPDefinitionHistory.created_at.desc()).first()

    if not latest_run:
        return PipelineStatusMetrics(
            status_metrics={
                "total_runs": {"label": "Total Pipeline Runs", "value": 0},
                "successful_runs": {"label": "Successful Runs", "value": 0},
                "failed_runs": {"label": "Failed Runs", "value": 0},
                "last_run_status": {"label": "Last Run Status", "value": "N/A"},
            },
            overall_status="N/A",
            last_updated=datetime.now()
        )

    # Calculate metrics
    total_runs = db.query(func.count(MCPDefinitionHistory.id)).scalar()
    successful_runs = db.query(func.count(MCPDefinitionHistory.id)).filter(MCPDefinitionHistory.status == "success").scalar()
    failed_runs = db.query(func.count(MCPDefinitionHistory.id)).filter(MCPDefinitionHistory.status == "failed").scalar()

    status_metrics = {
        "total_runs": {"label": "Total Pipeline Runs", "value": total_runs},
        "successful_runs": {"label": "Successful Runs", "value": successful_runs},
        "failed_runs": {"label": "Failed Runs", "value": failed_runs},
        "last_run_status": {"label": "Last Run Status", "value": latest_run.status},
    }

    # Determine overall status
    if failed_runs > 0:
        overall_status = "warning"
    elif successful_runs > 0:
        overall_status = "success"
    else:
        overall_status = "unknown"

    return PipelineStatusMetrics(
        status_metrics=status_metrics,
        overall_status=overall_status,
        last_updated=latest_run.created_at
    )

@router.get("/risk-tier-definition-history/pipeline-status", response_model=PipelineStatusMetrics)
async def get_pipeline_status(db: Session = Depends(get_session)):
    return get_pipeline_status_metrics(db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.models import Base

    # Create a test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    test_app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        session.add(MCPDefinitionHistory(
            status="success",
            created_at=datetime.now()
        ))
        session.add(MCPDefinitionHistory(
            status="failed",
            created_at=datetime.now()
        ))
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/risk-tier-definition-history/pipeline-status")
    assert response.status_code == 200
    assert response.json()["status_metrics"]["total_runs"]["value"] == 2
    assert response.json()["status_metrics"]["successful_runs"]["value"] == 1
    assert response.json()["status_metrics"]["failed_runs"]["value"] == 1
    assert response.json()["overall_status"] == "warning"
    print("PASS")