from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective

def signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal_scores")
    async def get_signal_scores(db: Session = Depends(get_session)):
        perspectives = db.query(Perspective).all()
        return {"perspectives": [{"id": p.id, "name": p.name} for p in perspectives]}

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override the dependency to use the in-memory database
    def override_get_session() -> Session:
        return Session(engine)

    test_app = signal_scores_endpoint()
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Test the endpoint
    response = client.get("/signal_scores")
    assert response.status_code == 200
    assert response.json() == {"perspectives": []}

    print("PASS")