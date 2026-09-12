from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

class SignalScoreRequest(BaseModel):
    server_ids: List[int]

class SignalScoreResponse(BaseModel):
    server_id: int
    signal_scores: dict
    mesh_memory: dict

class MeshMemoryResponse(BaseModel):
    mesh_memory: dict

class MeshScoresResponse(BaseModel):
    mesh_scores: dict

def get_mesh_memory(server_id: int, session=Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(server_ids: List[int], session=Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN ({','.join(map(str, server_ids))})"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/signal_scores", response_model=List[SignalScoreResponse])
async def signal_scores_endpoint(request: SignalScoreRequest, session=Depends(get_session)):
    signal_scores = get_signal_scores(request.server_ids, session)
    results = []
    for score in signal_scores:
        mesh_memory = get_mesh_memory(score["server_id"], session)
        results.append({
            "server_id": score["server_id"],
            "signal_scores": score,
            "mesh_memory": mesh_memory
        })
    return results

@app.get("/mesh_memory/{server_id}", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(server_id: int, session=Depends(get_session)):
    mesh_memory = get_mesh_memory(server_id, session)
    return {"mesh_memory": mesh_memory}

@app.get("/mesh_scores/{server_id}", response_model=MeshScoresResponse)
async def mesh_scores_endpoint(server_id: int, session=Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return {"mesh_scores": response.json()}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import sqlite3
    from app.db import get_session
    from app.models import Base

    # Override the session for self-test
    test_engine = sqlite3.connect(":memory:")
    Base.metadata.create_all(test_engine)
    app.dependency_overrides[get_session] = lambda: test_engine

    # Mock data for self-test
    test_server = McpServerRegistry(server_id=1, hostname="test-server")
    test_session = get_session()
    test_session.add(test_server)
    test_session.commit()

    # Run self-test
    try:
        response = requests.post(
            "http://127.0.0.1:8000/signal_scores",
            json={"server_ids": [1]},
            timeout=10
        )
        if response.status_code == 200:
            print("PASS")
        else:
            print("FAIL")
    except requests.exceptions.RequestException:
        print("FAIL")