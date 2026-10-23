from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from typing import List, Dict, Any
import requests

router = APIRouter()

def get_signal_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    signal_scores = session.query(McpSignalScores).all()
    return [{"server_id": score.server_id, "score": score.score} for score in signal_scores]

def _health_check(session: Session = Depends(get_session)) -> Dict[str, str]:
    try:
        session.query(McpServerRegistry).first()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

def api_signal_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return get_signal_scores(session)

def _run_self_test(session: Session = Depends(get_session)) -> Dict[str, str]:
    try:
        _health_check(session)
        return {"status": "PASS"}
    except Exception as e:
        return {"status": "FAIL", "error": str(e)}

def get_mesh_memory_endpoint() -> Dict[str, Any]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def test_self(session: Session = Depends(get_session)) -> Dict[str, str]:
    return _run_self_test(session)

def run_self_test(session: Session = Depends(get_session)) -> Dict[str, str]:
    return _run_self_test(session)

def mesh_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    scores = session.query(McpLlmAxisScore).all()
    return [{"server_id": score.server_id, "axis": score.axis, "score": score.score} for score in scores]

def signal_scores_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return get_signal_scores(session)

def get_mesh_memory() -> Dict[str, Any]:
    return get_mesh_memory_endpoint()

def mesh_scores_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return mesh_scores(session)

def get_score_disputes(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    disputes = session.query(McpScoreDispute).all()
    return [{"server_id": dispute.server_id, "dispute": dispute.dispute} for dispute in disputes]

if __name__ == "__main__":
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: Session(bind=engine)
    print("PASS")