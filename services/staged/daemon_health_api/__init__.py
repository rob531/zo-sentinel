from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def test_self():
    app = FastAPI()
    engine = create_engine('sqlite:///:memory:', poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    async def test():
        return {"message": "PASS"}

    client = TestClient(app)
    response = client.get("/test")
    assert response.json() == {"message": "PASS"}
    print("PASS")

if __name__ == "__main__":
    test_self()