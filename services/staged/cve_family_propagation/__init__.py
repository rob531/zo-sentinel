from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session

app = FastAPI()

class SignalScoresRequest(BaseModel):
    server_id: int
    signal_type: str

class MeshMemoryRequest(BaseModel):
    server_id: int

class DummyPostRequest(BaseModel):
    data: str

class ResetQuarantineRequest(BaseModel):
    server_id: int

def get_signal_scores(request: SignalScoresRequest, db: Session = Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {request.server_id} AND signal_type = '{request.signal_type}';"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

def get_mesh_memory(request: MeshMemoryRequest, db: Session = Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {request.server_id};"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

def dummy_post_endpoint(request: DummyPostRequest, db: Session = Depends(get_session)):
    return {"status": "success", "data": request.data}

def reset_server_export_api_quarantine(request: ResetQuarantineRequest, db: Session = Depends(get_session)):
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == request.server_id).first()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    server.export_api_quarantine = False
    db.commit()
    return {"status": "success", "server_id": request.server_id}

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    try:
        test_server = McpServerRegistry(id=1, hostname="test-server", export_api_quarantine=False)
        test_session.add(test_server)
        test_session.commit()

        test_signal = get_signal_scores(SignalScoresRequest(server_id=1, signal_type="test"))
        test_mesh = get_mesh_memory(MeshMemoryRequest(server_id=1))
        test_dummy = dummy_post_endpoint(DummyPostRequest(data="test"))
        test_reset = reset_server_export_api_quarantine(ResetQuarantineRequest(server_id=1))

        if test_signal and test_mesh and test_dummy and test_reset:
            print("PASS")
        else:
            print("FAIL")
    finally:
        test_session.close()
        app.dependency_overrides.clear()

if __name__ == "__main__":
    _run_self_test()