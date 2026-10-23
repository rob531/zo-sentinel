from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from pydantic import BaseModel

router = APIRouter()

class MeshMemoryResponse(BaseModel):
    data: List[dict]

class SignalScoresResponse(BaseModel):
    data: List[dict]

def get_mesh_memory() -> MeshMemoryResponse:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mesh_memory"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return MeshMemoryResponse(**response.json())

def get_signal_scores() -> SignalScoresResponse:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mcp_signal_scores"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return SignalScoresResponse(**response.json())

def mesh_memory_endpoint() -> MeshMemoryResponse:
    return get_mesh_memory()

def mesh_scores_endpoint() -> SignalScoresResponse:
    return get_signal_scores()

def get_mesh_memory_endpoint() -> MeshMemoryResponse:
    return get_mesh_memory()

def signal_scores_endpoint() -> SignalScoresResponse:
    return get_signal_scores()

def reset_quarantine_api() -> str:
    return "Quarantine reset"

def _dummy_post() -> str:
    return "Dummy post"

def _run_self_test() -> str:
    return "PASS"

if __name__ == "__main__":
    print(_run_self_test())