from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Optional
import json

app = FastAPI()

def get_mesh_data(endpoint: str) -> dict:
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": endpoint}, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data from mesh: {str(e)}")

@app.get("/servers")
def get_servers(db: Session = Depends(get_session)) -> List[dict]:
    servers = db.query(McpServerRegistry).all()
    return [{"id": server.id, "name": server.name, "status": server.status} for server in servers]

@app.get("/scores")
def get_scores(db: Session = Depends(get_session)) -> List[dict]:
    scores = db.query(McpLlmAxisScore).all()
    return [{"id": score.id, "server_id": score.server_id, "axis": score.axis, "value": score.value} for score in scores]

@app.get("/disputes")
def get_disputes(db: Session = Depends(get_session)) -> List[dict]:
    disputes = db.query(McpScoreDispute).all()
    return [{"id": dispute.id, "score_id": dispute.score_id, "reason": dispute.reason} for dispute in disputes]

@app.get("/orgs")
def get_orgs(db: Session = Depends(get_session)) -> List[dict]:
    orgs = db.query(Org).all()
    return [{"id": org.id, "name": org.name} for org in orgs]

@app.get("/users")
def get_users(db: Session = Depends(get_session)) -> List[dict]:
    users = db.query(User).all()
    return [{"id": user.id, "name": user.name, "org_id": user.org_id} for user in users]

@app.get("/mesh/scores")
def get_mesh_scores() -> dict:
    return get_mesh_data("mcp_signal_scores")

@app.get("/mesh/memory")
def get_mesh_memory() -> dict:
    return get_mesh_data("mesh_memory")

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Test the endpoints
    try:
        client = uvicorn.testing.TestClient(app)
        response = client.get("/servers")
        assert response.status_code == 200
        response = client.get("/scores")
        assert response.status_code == 200
        response = client.get("/disputes")
        assert response.status_code == 200
        response = client.get("/orgs")
        assert response.status_code == 200
        response = client.get("/users")
        assert response.status_code == 200
        response = client.get("/mesh/scores")
        assert response.status_code == 200
        response = client.get("/mesh/memory")
        assert response.status_code == 200
        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")