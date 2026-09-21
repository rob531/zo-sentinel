from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, Org, User

router = APIRouter()


class SignalScoresRequest(BaseModel):
    org_id: Optional[int] = None
    signal_type: Optional[str] = None


class MeshScoresRequest(BaseModel):
    org_id: Optional[int] = None


class MeshMemoryRequest(BaseModel):
    id: int


class AxisScoresRequest(BaseModel):
    org_id: Optional[int] = None


def signal_scores_endpoint(request: SignalScoresRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint for signal scores data."""
    query = {"table": "mcp_signal_scores", "filters": {}}
    if request.org_id:
        query["filters"]["org_id"] = request.org_id
    if request.signal_type:
        query["filters"]["signal_type"] = request.signal_type
    try:
        import requests
        response = requests.post("http://127.0.0.1:8772/query", json=query, timeout=5)
        if response.status_code == 200:
            return {"data": response.json().get("results", [])}
    except Exception:
        pass
    return {"data": []}


def llm_axis_scores_endpoint(request: AxisScoresRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint for LLM axis scores."""
    query = {"table": "mcp_llm_axis_scores", "filters": {}}
    if request.org_id:
        query["filters"]["org_id"] = request.org_id
    try:
        import requests
        response = requests.post("http://127.0.0.1:8772/query", json=query, timeout=5)
        if response.status_code == 200:
            return {"data": response.json().get("results", [])}
    except Exception:
        pass
    return {"data": []}


def get_signal_scores(org_id: Optional[int] = None, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get signal scores from mesh bus."""
    query = {"table": "mcp_signal_scores", "filters": {}}
    if org_id:
        query["filters"]["org_id"] = org_id
    try:
        import requests
        response = requests.post("http://127.0.0.1:8772/query", json=query, timeout=5)
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception:
        pass
    return []


def mesh_scores_endpoint(request: MeshScoresRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint for mesh scores."""
    return signal_scores_endpoint(SignalScoresRequest(org_id=request.org_id), session)


def mesh_scores(org_id: Optional[int] = None, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get mesh scores from mesh bus."""
    return get_signal_scores(org_id, session)


def get_mesh_memory(org_id: Optional[int] = None, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get mesh memory records from mesh bus."""
    query = {"table": "mesh_memory", "filters": {}}
    if org_id:
        query["filters"]["org_id"] = org_id
    try:
        import requests
        response = requests.post("http://127.0.0.1:8772/query", json=query, timeout=5)
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception:
        pass
    return []


def get_mesh_memory_by_id(item_id: int, session: Session = Depends(get_session)) -> Optional[Dict[str, Any]]:
    """Get mesh memory record by ID."""
    query = {"table": "mesh_memory", "filters": {"id": item_id}}
    try:
        import requests
        response = requests.post("http://127.0.0.1:8772/query", json=query, timeout=5)
        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[0] if results else None
    except Exception:
        pass
    return None


def mesh_memory_endpoint(request: MeshMemoryRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint for mesh memory by ID."""
    item = get_mesh_memory_by_id(request.id, session)
    if not item:
        raise HTTPException(status_code=404, detail="Mesh memory item not found")
    return item


def orgs_endpoint(org_id: Optional[int] = None, session: Session = Depends(get_session)) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Endpoint for orgs data."""
    if org_id:
        org = session.query(Org).filter(Org.id == org_id).first()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        return {"id": org.id, "name": org.name, "status": org.status}
    orgs = session.query(Org).all()
    return [{"id": org.id, "name": org.name, "status": org.status} for org in orgs]


def _run_self_test():
    import sys
    from io import StringIO
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_app = FastAPI()
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    @test_app.get("/test/signal_scores")
    def test_signal_endpoint():
        return signal_scores_endpoint(SignalScoresRequest(), next(override_get_session()))
    
    @test_app.get("/test/orgs")
    def test_orgs_endpoint():
        return orgs_endpoint(session=next(override_get_session()))
    
    @test_app.get("/test/mesh_scores")
    def test_mesh_scores():
        return mesh_scores_endpoint(MeshScoresRequest(), next(override_get_session()))
    
    @test_app.get("/test/mesh_memory/{item_id}")
    def test_mesh_memory_endpoint(item_id: int):
        return mesh_memory_endpoint(MeshMemoryRequest(id=item_id), next(override_get_session()))
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    try:
        r1 = client.get("/test/signal_scores")
        assert r1.status_code == 200, f"signal_scores_endpoint failed: {r1.status_code}"
        
        r2 = client.get("/test/orgs")
        assert r2.status_code == 200, f"orgs_endpoint failed: {r2.status_code}"
        
        r3 = client.get("/test/mesh_scores")
        assert r3.status_code == 200, f"mesh_scores_endpoint failed: {r3.status_code}"
        
        r4 = client.get("/test/mesh_memory/1")
        assert r4.status_code == 404, f"mesh_memory_endpoint should return 404 for missing: {r4.status_code}"
        
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        test_app.dependency_overrides.clear()


if __name__ == "__main__":
    _run_self_test()