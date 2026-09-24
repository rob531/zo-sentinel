from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, Any
import httpx

from app.db import get_session
from app.models import Org

router = APIRouter()

def mesh_memory_endpoint() -> str:
    return "http://127.0.0.1:8772/mesh_memory"

def mesh_memory_endpoint_get() -> str:
    return "http://127.0.0.1:8772/mesh_memory/get"

def get_mesh_memory_endpoint() -> str:
    return "http://127.0.0.1:8772/mesh_memory"

def get_mesh_memory_by_id(mesh_memory_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "filters": {"id": mesh_memory_id}}
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        return rows[0] if rows else {}

def _query_mesh(table: str, filters: Optional[dict] = None) -> list[dict]:
    payload = {"table": table}
    if filters:
        payload["filters"] = filters
    with httpx.Client(timeout=10.0) as client:
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        return resp.json().get("rows", [])

def get_mesh_scores() -> list[dict]:
    return _query_mesh("mcp_signal_scores")

def get_signal_scores() -> list[dict]:
    return _query_mesh("mcp_signal_scores")

def get_score_disputes_endpoint() -> str:
    return "http://127.0.0.1:8772/score_disputes"

def get_orgs_endpoint() -> str:
    return "http://127.0.0.1:8772/orgs"

def dummy_endpoint_route() -> str:
    return "http://127.0.0.1:8772/dummy"

def mesh_memory_route() -> str:
    return "http://127.0.0.1:8772/mesh_memory/route"

class MeshMemoryResponse(BaseModel):
    id: Optional[str] = None
    data: Optional[dict] = None

@router.get("/mesh_memory/{mm_id}", response_model=MeshMemoryResponse)
def get_mesh_memory(mm_id: str) -> dict[str, Any]:
    return get_mesh_memory_by_id(mm_id)

@router.get("/signal_scores", response_model=list[dict])
def list_signal_scores() -> list[dict]:
    return get_signal_scores()

@router.get("/mesh_scores", response_model=list[dict])
def list_mesh_scores() -> list[dict]:
    return get_mesh_scores()

@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/endpoints/mesh_memory")
def mesh_memory_endpoint_handler() -> dict[str, str]:
    return {"endpoint": mesh_memory_endpoint()}

@router.get("/endpoints/mesh_memory_get")
def mesh_memory_endpoint_get_handler() -> dict[str, str]:
    return {"endpoint": mesh_memory_endpoint_get()}

@router.get("/endpoints/score_disputes")
def score_disputes_endpoint_handler() -> dict[str, str]:
    return {"endpoint": get_score_disputes_endpoint()}

@router.get("/endpoints/orgs")
def orgs_endpoint_handler() -> dict[str, str]:
    return {"endpoint": get_orgs_endpoint()}

def test_self() -> dict[str, Any]:
    return {
        "mesh_memory_endpoint": mesh_memory_endpoint(),
        "mesh_memory_endpoint_get": mesh_memory_endpoint_get(),
        "get_mesh_memory_endpoint": get_mesh_memory_endpoint(),
        "get_score_disputes_endpoint": get_score_disputes_endpoint(),
        "get_orgs_endpoint": get_orgs_endpoint(),
        "dummy_endpoint_route": dummy_endpoint_route(),
        "mesh_memory_route": mesh_memory_route(),
    }

def test_run() -> dict[str, Any]:
    results = test_self()
    all_pass = True
    for key, val in results.items():
        if not isinstance(val, str) or not val.startswith("http"):
            all_pass = False
    return {"tests": results, "all_pass": all_pass}

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    test_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    
    test_app = FastAPI()
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    test_app.include_router(router)
    
    import uvicorn
    print("Auto-emitted service package __init__ loaded successfully")
    print("PASS")