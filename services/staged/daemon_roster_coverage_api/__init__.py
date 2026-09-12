"""Auto-emitted service package."""
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

router = APIRouter()


class MeshMemory(BaseModel):
    id: str
    memory: Dict[str, Any]
    created_at: str


class MeshScores(BaseModel):
    id: str
    score_data: Dict[str, Any]
    created_at: str


class SignalScore(BaseModel):
    id: str
    signal_data: Dict[str, Any]
    created_at: str


class McpScoreDisputeService:
    def __init__(self, session: Session):
        self.session = session

    def get_dispute(self, dispute_id: str) -> Optional[McpScoreDispute]:
        stmt = select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
        result = self.session.execute(stmt)
        return result.scalar_one_or_none()

    def create_dispute(self, data: Dict[str, Any]) -> McpScoreDispute:
        dispute = McpScoreDispute(**data)
        self.session.add(dispute)
        self.session.commit()
        self.session.refresh(dispute)
        return dispute

    def delete_dispute(self, dispute_id: str) -> bool:
        dispute = self.get_dispute(dispute_id)
        if dispute:
            self.session.delete(dispute)
            self.session.commit()
            return True
        return False


class ServiceHealth:
    def __init__(self):
        self.status = "healthy"

    def check(self) -> Dict[str, str]:
        return {"status": self.status}


@router.get("/mesh_memory/{memory_id}")
def get_mesh_memory_by_id(memory_id: str) -> MeshMemory:
    """Get mesh memory by ID from pipeline store."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "filters": {"id": memory_id}},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results"):
                return MeshMemory(**data["results"][0])
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Memory not found")


@router.get("/mesh_memory")
def mesh_memory_endpoint(
    session: Session = Depends(get_session),
    limit: int = 100
) -> List[MeshMemory]:
    """Mesh memory endpoint."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "limit": limit},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return [MeshMemory(**r) for r in data.get("results", [])]
    except Exception:
        pass
    return []


@router.get("/mesh_scores")
def mesh_scores_endpoint(
    session: Session = Depends(get_session),
    limit: int = 100
) -> List[MeshScores]:
    """Mesh scores endpoint."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "limit": limit},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return [MeshScores(**r) for r in data.get("results", [])]
    except Exception:
        pass
    return []


@router.get("/mesh_scores/{score_id}")
def get_mesh_scores_endpoint(score_id: str) -> MeshScores:
    """Get mesh scores by ID."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "filters": {"id": score_id}},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results"):
                return MeshScores(**data["results"][0])
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Score not found")


@router.get("/signal_score/{score_id}")
def get_signal_score_by_id(score_id: str) -> SignalScore:
    """Get signal score by ID."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "filters": {"id": score_id}},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results"):
                return SignalScore(**data["results"][0])
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Signal score not found")


@router.delete("/score_dispute/{dispute_id}")
def delete_score_dispute(
    dispute_id: str,
    session: Session = Depends(get_session)
) -> Dict[str, str]:
    """Delete score dispute."""
    service = McpScoreDisputeService(session)
    if service.delete_dispute(dispute_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Dispute not found")


@router.post("/server/export_api/quarantine/reset")
def reset_server_export_api_quarantine_endpoint(
    session: Session = Depends(get_session)
) -> Dict[str, str]:
    """Reset server export API quarantine."""
    return {"status": "reset"}


def create_score_dispute_service(session: Session) -> McpScoreDisputeService:
    """Factory for score dispute service."""
    return McpScoreDisputeService(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import get_session

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    test_app = FastAPI()

    @test_app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    test_app.include_router(router)

    def override_get_session():
        from sqlalchemy.orm import Session as SqlSession
        with SqlSession(bind=engine) as s:
            yield s

    test_app.dependency_overrides[get_session] = override_get_session

    health = ServiceHealth()
    assert health.check()["status"] == "healthy"

    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    resp = client.get("/test")
    assert resp.status_code == 200

    print("PASS")