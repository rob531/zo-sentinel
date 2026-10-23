from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from typing import List, Optional
import requests

def get_mesh_scores_endpoint():
    """Fetch mesh scores from the ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def get_signal_scores(db: Session = Depends(get_session)):
    """Fetch signal scores from the app database."""
    return db.query(McpLlmAxisScore).all()

def get_mesh_memory_endpoint():
    """Fetch mesh memory from the ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def signal_scores_endpoint(db: Session = Depends(get_session)):
    """Endpoint to fetch signal scores."""
    return get_signal_scores(db)

def mesh_scores_endpoint(db: Session = Depends(get_session)):
    """Endpoint to fetch mesh scores."""
    return get_mesh_scores_endpoint()

def _run_self_test():
    """Self-test for the service."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

if __name__ == "__main__":
    _run_self_test()