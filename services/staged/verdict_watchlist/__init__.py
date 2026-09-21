from fastapi import FastAPI, Depends, HTTPException
from typing import List, Dict, Optional
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session

app = FastAPI()

class MeshScoresRequest(BaseModel):
    server_ids: List[int]

class MeshMemoryRequest(BaseModel):
    server_ids: List[int]

class SignalScoresRequest(BaseModel):
    server_ids: List[int]

class ServerExportApiQuarantineRequest(BaseModel):
    server_ids: List[int]

class MeshScoresResponse(BaseModel):
    server_id: int
    scores: Dict[str, float]

class MeshMemoryResponse(BaseModel):
    server_id: int
    memory: Dict[str, float]

class SignalScoresResponse(BaseModel):
    server_id: int
    scores: Dict[str, float]

class ServerExportApiQuarantineResponse(BaseModel):
    server_id: int
    status: str

def get_mesh_scores(server_ids: List[int]) -> List[MeshScoresResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN {tuple(server_ids)}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh scores")
    data = response.json()
    return [MeshScoresResponse(server_id=row["server_id"], scores=row["scores"]) for row in data]

def get_mesh_memory(server_ids: List[int]) -> List[MeshMemoryResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id IN {tuple(server_ids)}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    data = response.json()
    return [MeshMemoryResponse(server_id=row["server_id"], memory=row["memory"]) for row in data]

def get_signal_scores(server_ids: List[int]) -> List[SignalScoresResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN {tuple(server_ids)}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    data = response.json()
    return [SignalScoresResponse(server_id=row["server_id"], scores=row["scores"]) for row in data]

def reset_server_export_api_quarantine(server_ids: List[int], db: Session = Depends(get_session)) -> List[ServerExportApiQuarantineResponse]:
    servers = db.query(McpServerRegistry).filter(McpServerRegistry.id.in_(server_ids)).all()
    for server in servers:
        server.export_api_quarantine = False
    db.commit()
    return [ServerExportApiQuarantineResponse(server_id=server.id, status="reset") for server in servers]

@app.post("/mesh_scores", response_model=List[MeshScoresResponse])
async def mesh_scores_endpoint(request: MeshScoresRequest):
    return get_mesh_scores(request.server_ids)

@app.post("/mesh_memory", response_model=List[MeshMemoryResponse])
async def mesh_memory_endpoint(request: MeshMemoryRequest):
    return get_mesh_memory(request.server_ids)

@app.post("/signal_scores", response_model=List[SignalScoresResponse])
async def signal_scores_endpoint(request: SignalScoresRequest):
    return get_signal_scores(request.server_ids)

@app.post("/reset_server_export_api_quarantine", response_model=List[ServerExportApiQuarantineResponse])
async def reset_server_export_api_quarantine_endpoint(request: ServerExportApiQuarantineRequest, db: Session = Depends(get_session)):
    return reset_server_export_api_quarantine(request.server_ids, db)

def _run_self_test():
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Test data
    test_server = McpServerRegistry(id=1, export_api_quarantine=True)
    db = SessionLocal()
    db.add(test_server)
    db.commit()

    # Test functions
    try:
        # Test get_mesh_scores
        mesh_scores = get_mesh_scores([1])
        assert len(mesh_scores) == 1

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory([1])
        assert len(mesh_memory) == 1

        # Test get_signal_scores
        signal_scores = get_signal_scores([1])
        assert len(signal_scores) == 1

        # Test reset_server_export_api_quarantine
        reset = reset_server_export_api_quarantine([1], db)
        assert reset[0].status == "reset"
        assert not test_server.export_api_quarantine

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    _run_self_test()