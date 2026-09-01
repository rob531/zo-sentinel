from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import User
from pydantic import BaseModel
import requests
from fastapi.responses import JSONResponse

app = FastAPI()

class MeshScore(BaseModel):
    entity_id: str
    score: float
    timestamp: str

class SignalScore(BaseModel):
    entity_id: str
    score: float
    timestamp: str

class MeshMemory(BaseModel):
    entity_id: str
    memory: str
    timestamp: str

def get_mesh_scores() -> List[MeshScore]:
    """Fetch mesh scores from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT entity_id, score, timestamp FROM mcp_signal_scores"},
        timeout=10
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return [MeshScore(**item) for item in response.json()]

def get_signal_scores() -> List[SignalScore]:
    """Fetch signal scores from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT entity_id, score, timestamp FROM mcp_signal_scores"},
        timeout=10
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return [SignalScore(**item) for item in response.json()]

def get_mesh_memory() -> List[MeshMemory]:
    """Fetch mesh memory from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT entity_id, memory, timestamp FROM mesh_memory"},
        timeout=10
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return [MeshMemory(**item) for item in response.json()]

def mesh_scores_endpoint() -> JSONResponse:
    """Endpoint to fetch mesh scores."""
    scores = get_mesh_scores()
    return JSONResponse(content=[score.dict() for score in scores])

def signal_scores_endpoint() -> JSONResponse:
    """Endpoint to fetch signal scores."""
    scores = get_signal_scores()
    return JSONResponse(content=[score.dict() for score in scores])

def mesh_memory_endpoint() -> JSONResponse:
    """Endpoint to fetch mesh memory."""
    memory = get_mesh_memory()
    return JSONResponse(content=[mem.dict() for mem in memory])

def _run_self_test(db: Session = Depends(get_session)) -> str:
    """Self-test for the module."""
    try:
        # Test database connection
        db.query(User).first()

        # Test mesh scores endpoint
        mesh_scores_endpoint()

        # Test signal scores endpoint
        signal_scores_endpoint()

        # Test mesh memory endpoint
        mesh_memory_endpoint()

        return "PASS"
    except Exception as e:
        return f"FAIL: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    print(_run_self_test())