from __future__ import annotations

import json
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpDefinitionHistory

router = APIRouter(prefix="/api", tags=["definition-history"])

class PipelineStatusAnalysis(BaseModel):
    status: str
    count: int
    percentage: float

@router.get("/definition-history-pipeline-status", response_model=list[PipelineStatusAnalysis])
def get_pipeline_status_analysis(db: Session = Depends(get_session)) -> list[PipelineStatusAnalysis]:
    """Get pipeline status analysis from mcp_definition_history."""
    try:
        # Get total count of records
        total_count = db.execute(select(func.count()).select_from(McpDefinitionHistory)).scalar() or 0

        # Get count per status
        status_counts = db.execute(
            select(
                McpDefinitionHistory.status,
                func.count().label("count")
            ).group_by(McpDefinitionHistory.status)
        ).all()

        # Calculate percentage and create analysis list
        analysis = [
            PipelineStatusAnalysis(
                status=status,
                count=count,
                percentage=(count / total_count) * 100 if total_count > 0 else 0
            )
            for status, count in status_counts
        ]

        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Create in-memory SQLite database
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed test data
    s = TS()
    test_data = [
        McpDefinitionHistory(id=1, status="pending"),
        McpDefinitionHistory(id=2, status="pending"),
        McpDefinitionHistory(id=3, status="completed"),
        McpDefinitionHistory(id=4, status="failed"),
        McpDefinitionHistory(id=5, status="failed"),
        McpDefinitionHistory(id=6, status="failed"),
    ]
    s.add_all(test_data)
    s.commit()
    s.close()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Override get_session dependency
    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    # Run tests
    c = TestClient(app)
    r = c.get("/api/definition-history-pipeline-status")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 3, j  # pending, completed, failed
    assert j[0]["status"] == "pending", j
    assert j[0]["count"] == 2, j
    assert j[0]["percentage"] == 50.0, j
    assert j[1]["status"] == "completed", j
    assert j[1]["count"] == 1, j
    assert j[1]["percentage"] == 16.666666666666668, j
    assert j[2]["status"] == "failed", j
    assert j[2]["count"] == 3, j
    assert j[2]["percentage"] == 50.0, j

    print("PASS")