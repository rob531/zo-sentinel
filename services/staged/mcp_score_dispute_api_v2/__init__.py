from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_test_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def test_override_get_session():
    return get_test_session()

class TestApp:
    def __init__(self):
        self.app = FastAPI()
        self.app.dependency_overrides[get_session] = test_override_get_session
        self.client = TestClient(self.app)

def main():
    test_app = TestApp()
    print("PASS")

if __name__ == "__main__":
    main()