from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

def test_self():
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create tables for testing
    McpServerRegistry.metadata.create_all(test_engine)
    McpLlmAxisScore.metadata.create_all(test_engine)
    McpScoreDispute.metadata.create_all(test_engine)
    Org.metadata.create_all(test_engine)
    User.metadata.create_all(test_engine)

    # Test the health endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    print("PASS")

if __name__ == "__main__":
    test_self()