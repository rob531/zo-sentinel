from typing import Any, Dict, List, Optional, Tuple
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

app = FastAPI()

class MeshMemoryResponse(BaseModel):
    data: List[Dict[str, Any]]

class MeshScoresResponse(BaseModel):
    data: List[Dict[str, Any]]

class SignalScoresResponse(BaseModel):
    data: List[Dict[str, Any]]

class LLMAxisScoresResponse(BaseModel):
    data: List[Dict[str, Any]]

def get_mesh_memory() -> List[Dict[str, Any]]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mesh_memory"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying mesh_memory")
    return response.json()["data"]

def get_mesh_scores() -> List[Dict[str, Any]]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mcp_signal_scores"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying mcp_signal_scores")
    return response.json()["data"]

def get_signal_scores() -> List[Dict[str, Any]]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mcp_signal_scores"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying mcp_signal_scores")
    return response.json()["data"]

def get_llm_axis_scores(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    scores = db.query(McpLlmAxisScore).all()
    return [{"id": score.id, "server_id": score.server_id, "axis": score.axis, "score": score.score} for score in scores]

def reset_quarantine(db: Session = Depends(get_session)) -> None:
    servers = db.query(McpServerRegistry).filter(McpServerRegistry.quarantined == True).all()
    for server in servers:
        server.quarantined = False
    db.commit()

def mesh_memory_endpoint() -> MeshMemoryResponse:
    return MeshMemoryResponse(data=get_mesh_memory())

def mesh_scores_endpoint() -> MeshScoresResponse:
    return MeshScoresResponse(data=get_mesh_scores())

def signal_scores_endpoint() -> SignalScoresResponse:
    return SignalScoresResponse(data=get_signal_scores())

def llm_axis_scores_endpoint(db: Session = Depends(get_session)) -> LLMAxisScoresResponse:
    return LLMAxisScoresResponse(data=get_llm_axis_scores(db))

def reset_quarantine_endpoint(db: Session = Depends(get_session)) -> None:
    reset_quarantine(db)

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    test_server = McpServerRegistry(server_id="test_server", quarantined=True)
    test_session.add(test_server)
    test_session.commit()

    reset_quarantine()
    quarantined_servers = test_session.query(McpServerRegistry).filter(McpServerRegistry.quarantined == True).all()
    if len(quarantined_servers) != 0:
        print("FAIL")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()