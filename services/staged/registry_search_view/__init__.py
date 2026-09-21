from fastapi import FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Override for self-test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = get_app()
    app.dependency_overrides[get_session] = override_get_session

    # Create tables for self-test
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Test health endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    print("PASS")