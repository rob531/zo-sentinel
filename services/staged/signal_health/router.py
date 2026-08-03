from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_signal_health

router = APIRouter(prefix="/api")


@router.get("/signals/health")
def read_signal_health(session: Session = Depends(get_session)):
    return get_signal_health(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timezone
    from app.db import Base, get_session as original_get_session
    from app.models import McpSignalScore

    engine = create_engine("sqlite:///:memory:", echo=False)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                McpSignalScore(server_id=1, signal_id=1, updated_at=datetime(2023, 1, 1, tzinfo=timezone.utc)),
                McpSignalScore(server_id=1, signal_id=2, updated_at=datetime(2023, 1, 2, tzinfo=timezone.utc)),
                McpSignalScore(server_id=1, signal_id=3, updated_at=datetime(2023, 1, 3, tzinfo=timezone.utc)),
                McpSignalScore(server_id=2, signal_id=4, updated_at=datetime(2023, 1, 4, tzinfo=timezone.utc)),
                McpSignalScore(server_id=2, signal_id=5, updated_at=datetime(2023, 1, 5, tzinfo=timezone.utc)),
                McpSignalScore(server_id=2, signal_id=6, updated_at=datetime(2023, 1, 6, tzinfo=timezone.utc)),
            ]
        )
        db.commit()

    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[original_get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/api/signals/health")
    assert response.status_code == 200
    data = response.json()
    assert data["servers"]["1"]["signal_count"] == 3
    assert data["servers"]["2"]["signal_count"] == 3
    print("PASS")