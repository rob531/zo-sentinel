from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    Perspective,
    AskCorpusDoc,
    VulnAdvisory,
)

def mesh_memory_endpoint():
    return "http://127.0.0.1:8772/query"

def get_test_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def test_dependency_override():
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    return app

def main():
    client = TestClient(test_dependency_override())
    response = client.get("/")
    assert response.status_code == 404
    print("PASS")

if __name__ == "__main__":
    main()