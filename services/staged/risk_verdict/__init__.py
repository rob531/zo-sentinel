from typing import Any, Dict, List, Optional, Tuple
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel

app = FastAPI()

class MeshScoresResponse(BaseModel):
    scores: Dict[str, Any]

class SignalScoresResponse(BaseModel):
    scores: Dict[str, Any]

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> MeshScoresResponse:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=5
        )
        response.raise_for_status()
        return MeshScoresResponse(scores=response.json())
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh scores: {str(e)}")

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> SignalScoresResponse:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=5
        )
        response.raise_for_status()
        return SignalScoresResponse(scores=response.json())
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching signal scores: {str(e)}")

def _post_query(query: str, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error executing query: {str(e)}")

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_server = McpServerRegistry(id=1, name="test_server")
    test_org = Org(id=1, name="test_org")
    test_user = User(id=1, name="test_user", org_id=1)

    with SessionLocal() as db:
        db.add(test_server)
        db.add(test_org)
        db.add(test_user)
        db.commit()

        # Test get_mesh_scores
        try:
            mesh_scores = get_mesh_scores(1)
            assert isinstance(mesh_scores, MeshScoresResponse)
        except Exception as e:
            print(f"Mesh scores test failed: {str(e)}")
            return

        # Test get_signal_scores
        try:
            signal_scores = get_signal_scores(1)
            assert isinstance(signal_scores, SignalScoresResponse)
        except Exception as e:
            print(f"Signal scores test failed: {str(e)}")
            return

        # Test _post_query
        try:
            query_result = _post_query("SELECT 1")
            assert isinstance(query_result, list)
        except Exception as e:
            print(f"Post query test failed: {str(e)}")
            return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()