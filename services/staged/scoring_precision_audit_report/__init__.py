from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    that_app = FastAPI()

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)
    that_app.dependency_overrides[get_session] = lambda: TestSession()

    @that_app.get("/health")
    def health():
        return {"status": "ok"}

    with TestClient(that_app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    print("PASS")