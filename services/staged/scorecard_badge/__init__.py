from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

app = FastAPI()

def get_mcp_signal_scores() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def get_mesh_memory() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

@app.get("/servers/")
async def get_servers(db: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return db.query(McpServerRegistry).all()

@app.get("/scores/")
async def get_scores(db: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    return db.query(McpLlmAxisScore).all()

@app.get("/disputes/")
async def get_disputes(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

@app.get("/orgs/")
async def get_orgs(db: Session = Depends(get_session)) -> List[Org]:
    return db.query(Org).all()

@app.get("/users/")
async def get_users(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

@app.get("/signal-scores/")
async def get_signal_scores() -> List[dict]:
    return get_mcp_signal_scores()

@app.get("/mesh-memory/")
async def get_mesh_memory() -> List[dict]:
    return get_mesh_memory()

if __name__ == "__main__":
    import sys
    from app.db import engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: sessionmaker(bind=engine)()

    # Test endpoints
    try:
        response = app.client.get("/servers/")
        assert response.status_code == 200
        response = app.client.get("/scores/")
        assert response.status_code == 200
        response = app.client.get("/disputes/")
        assert response.status_code == 200
        response = app.client.get("/orgs/")
        assert response.status_code == 200
        response = app.client.get("/users/")
        assert response.status_code == 200
        response = app.client.get("/signal-scores/")
        assert response.status_code == 200
        response = app.client.get("/mesh-memory/")
        assert response.status_code == 200
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)