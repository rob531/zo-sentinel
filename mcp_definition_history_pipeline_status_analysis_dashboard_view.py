from datetime import datetime, timedelta
from typing import Dict
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpDefinitionHistory

router = APIRouter()

@router.get("/definition-history/pipeline-status")
async def get_pipeline_status(session: Session = Depends(get_session)) -> Dict[str, int]:
    thirty_days_ago = datetime.now() - timedelta(days=30)
    pipeline_status = session.query(
        McpDefinitionHistory.pipeline_status,
        McpDefinitionHistory.created_at
    ).filter(
        McpDefinitionHistory.created_at >= thirty_days_ago
    ).all()

    status_counts = {}
    for status, created_at in pipeline_status:
        date_str = created_at.strftime("%Y-%m-%d")
        if date_str not in status_counts:
            status_counts[date_str] = {}
        if status not in status_counts[date_str]:
            status_counts[date_str][status] = 0
        status_counts[date_str][status] += 1

    return status_counts

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
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

    def seed_db():
        db = TestingSessionLocal()
        thirty_days_ago = datetime.now() - timedelta(days=30)
        for i in range(30):
            date = thirty_days_ago + timedelta(days=i)
            status = "success" if i % 2 == 0 else "failure"
            db.add(McpDefinitionHistory(pipeline_status=status, created_at=date))
        db.commit()
        db.close()

    seed_db()

    from app.db import get_session
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    test_app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(test_app)

    response = test_client.get("/definition-history/pipeline-status")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)
    print("PASS")