from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org

def signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal_scores")
    async def get_signal_scores(db: Session = Depends(get_session)):
        servers = db.query(McpServerRegistry).all()
        return [{"id": server.id, "name": server.name} for server in servers]

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    test_app = signal_scores_endpoint()
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    client = TestClient(test_app)
    response = client.get("/signal_scores")
    assert response.status_code == 200
    print("PASS")