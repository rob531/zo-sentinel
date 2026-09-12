from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User, McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org

def signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal_scores")
    def get_signal_scores(db: Session = Depends(get_session)):
        return {"message": "Signal scores endpoint"}

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.models import Base

    # Set up a test database
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override the dependency to use the test database
    test_app = signal_scores_endpoint()
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/signal_scores")
    assert response.status_code == 200
    assert response.json() == {"message": "Signal scores endpoint"}

    print("PASS")