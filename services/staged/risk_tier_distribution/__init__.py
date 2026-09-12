from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

def test_main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test the root endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}

    print("PASS")

if __name__ == "__main__":
    test_main()