from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import McpDefinitionHistory
from pydantic import BaseModel

router = APIRouter()

class PipelineStatusResponse(BaseModel):
    status: dict

@router.get("/definition-history-pipeline-status", response_model=PipelineStatusResponse)
def get_definition_history_pipeline_status(session: Session = Depends(get_session)):
    status_counts = session.query(
        McpDefinitionHistory.pipeline_status,
        func.count(McpDefinitionHistory.pipeline_status)
    ).group_by(McpDefinitionHistory.pipeline_status).all()

    status_dict = {status: count for status, count in status_counts}

    if "CRITICAL" in status_dict:
        status_dict["tier"] = "CRITICAL"

    return {"status": status_dict}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    test_data = [
        {"pipeline_status": "PENDING"},
        {"pipeline_status": "PENDING"},
        {"pipeline_status": "COMPLETED"},
        {"pipeline_status": "CRITICAL"},
    ]

    with override_get_session() as session:
        for data in test_data:
            session.add(McpDefinitionHistory(**data))
        session.commit()

    response = client.get("/definition-history-pipeline-status")
    assert response.status_code == 200
    assert response.json() == {
        "status": {
            "PENDING": 2,
            "COMPLETED": 1,
            "CRITICAL": 1,
            "tier": "CRITICAL"
        }
    }

    print("PASS")