from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Optional

router = APIRouter()

class PipelineStatus(BaseModel):
    status: str
    last_updated: Optional[str]
    items_processed: int
    error_rate: float

def get_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_definition_history import Base, PipelineStatus as DBPipelineStatus

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    # Seed test data
    test_data = DBPipelineStatus(
        status="running",
        last_updated="2023-01-01T00:00:00Z",
        items_processed=100,
        error_rate=0.05
    )
    session.add(test_data)
    session.commit()

    yield session
    session.close()

@router.get("/definition-history-pipeline-status", response_model=PipelineStatus)
async def get_pipeline_status(db: Session = Depends(get_db)):
    from mcp_definition_history import PipelineStatus as DBPipelineStatus

    result = db.execute(
        select(
            DBPipelineStatus.status,
            DBPipelineStatus.last_updated,
            DBPipelineStatus.items_processed,
            DBPipelineStatus.error_rate
        ).limit(1)
    ).fetchone()

    if result:
        return {
            "status": result[0],
            "last_updated": result[1],
            "items_processed": result[2],
            "error_rate": result[3]
        }
    return {"status": "unknown", "last_updated": None, "items_processed": 0, "error_rate": 0.0}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/definition-history-pipeline-status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "running",
        "last_updated": "2023-01-01T00:00:00Z",
        "items_processed": 100,
        "error_rate": 0.05
    }

    print("PASS")