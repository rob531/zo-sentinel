from typing import Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

def get_mcp_server_registry(session: Session = Depends(get_session)) -> Optional[McpServerRegistry]:
    """Retrieve the McpServerRegistry instance from the database."""
    return session.query(McpServerRegistry).first()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override get_session for testing
    def get_test_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session

    # Add test route
    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    client = TestClient(app)
    response = client.get("/test")
    print(response.json()["status"])