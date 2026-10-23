from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import httpx
from sqlalchemy import text
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()

class SignalScoreResponse(BaseModel):
    scores: List[Dict[str, Any]]
    mesh_memory: Optional[Dict[str, Any]] = None

def get_mesh_memory() -> Optional[Dict[str, Any]]:
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"table": "mesh_memory", "limit": 1}
            )
            response.raise_for_status()
            results = response.json()
            return results[0] if results else None
    except Exception:
        return None

def get_mesh_scores(org_id: int) -> List[Dict[str, Any]]:
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"table": "mcp_signal_scores", "org_id": org_id}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return []

def get_signal_scores(org_id: int, db=Depends(get_session)) -> SignalScoreResponse:
    mesh_memory = get_mesh_memory()
    mesh_scores = get_mesh_scores(org_id)
    
    result = db.execute(
        text("SELECT * FROM McpLlmAxisScore WHERE org_id = :org_id"),
        {"org_id": org_id}
    ).fetchall()
    
    app_scores = [dict(row._mapping) for row in result]
    
    return SignalScoreResponse(
        scores=mesh_scores + app_scores,
        mesh_memory=mesh_memory
    )

def api_signal_scores(org_id: int, db=Depends(get_session)) -> SignalScoreResponse:
    return get_signal_scores(org_id, db)

@router.get("/self_test")
def _run_self_test(db=Depends(get_session)) -> Dict[str, str]:
    try:
        mesh_memory = get_mesh_memory()
        assert mesh_memory is not None, "mesh_memory unavailable"
        
        result = db.execute(text("SELECT 1")).scalar()
        assert result == 1, "database connection failed"
        
        return {"status": "PASS"}
    except Exception as e:
        return {"status": f"FAIL: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    from app.db import get_session, Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    
    testing_session = Session(
        bind=create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
    )
    
    app.dependency_overrides[get_session] = lambda: testing_session
    
    uvicorn.run(app, host="0.0.0.0", port=8000)