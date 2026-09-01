from typing import Optional
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class SentinelService:
    def __init__(self, db_session: Session = Depends(get_session)):
        self.db_session = db_session

    def get_server_by_name(self, server_name: str) -> Optional[McpServerRegistry]:
        return self.db_session.query(McpServerRegistry).filter_by(server_name=server_name).first()

    def get_scores_by_server(self, server_id: int) -> list[McpLlmAxisScore]:
        return self.db_session.query(McpLlmAxisScore).filter_by(server_id=server_id).all()

    def get_disputes_by_score(self, score_id: int) -> list[McpScoreDispute]:
        return self.db_session.query(McpScoreDispute).filter_by(score_id=score_id).all()

    def get_org_by_id(self, org_id: int) -> Optional[Org]:
        return self.db_session.query(Org).filter_by(id=org_id).first()

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        return self.db_session.query(User).filter_by(id=user_id).first()

def create_app():
    app = FastAPI()

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Test setup
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(test_engine)

    from app.db import get_session as original_get_session
    from fastapi import Depends

    def override_get_session() -> Session:
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[original_get_session] = override_get_session

    client = TestClient(app)

    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    print("PASS")