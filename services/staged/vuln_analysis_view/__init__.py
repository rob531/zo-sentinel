from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal_scores")
    async def get_signal_scores(db: Session = Depends(get_session)):
        servers = db.query(McpServerRegistry).all()
        scores = db.query(McpLlmAxisScore).all()
        disputes = db.query(McpScoreDispute).all()
        return {
            "servers": [{"id": s.id, "name": s.name} for s in servers],
            "scores": [{"id": sc.id, "score": sc.score} for sc in scores],
            "disputes": [{"id": d.id, "status": d.status} for d in disputes]
        }

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    from app.db import get_session
    app = signal_scores_endpoint()
    app.dependency_overrides[get_session] = lambda: Session(engine)

    client = TestClient(app)
    response = client.get("/signal_scores")
    assert response.status_code == 200
    print("PASS")