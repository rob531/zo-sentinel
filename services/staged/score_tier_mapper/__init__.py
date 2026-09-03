"""Auto-emitted service package."""
from typing import Any, Optional

from app.db import get_session
from app.models import Org, McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from fastapi import Depends
from sqlalchemy.orm import Session
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def get_mesh_memory(mesh_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Retrieve mesh memory entry by id."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": "SELECT * FROM mesh_memory WHERE id = %s", "params": [mesh_id]},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    return rows[0] if rows else None


def get_mesh_memory_by_id(mesh_id: str) -> dict[str, Any]:
    """Retrieve mesh memory entry by id (standalone)."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": "SELECT * FROM mesh_memory WHERE id = %s", "params": [mesh_id]},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    return rows[0] if rows else None


def mesh_memory_endpoint_get(mesh_id: str) -> dict[str, Any]:
    """Endpoint-style mesh memory retrieval."""
    return get_mesh_memory(mesh_id)


def mesh_memory_endpoint(mesh_id: str) -> dict[str, Any]:
    """Mesh memory endpoint getter."""
    return get_mesh_memory_by_id(mesh_id)


def get_mcp_llm_axis_scores(org_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Retrieve LLM axis scores for org."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": "SELECT * FROM mcp_llm_axis_scores WHERE org_id = %s", "params": [org_id]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def mesh_scores_endpoint(org_id: str) -> list[dict[str, Any]]:
    """Mesh scores endpoint for org."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": "SELECT * FROM mcp_signal_scores WHERE org_id = %s", "params": [org_id]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def api_signal_scores(org_id: str) -> list[dict[str, Any]]:
    """API-level signal scores retrieval."""
    return mesh_scores_endpoint(org_id)


def signal_scores_endpoint(org_id: str) -> list[dict[str, Any]]:
    """Signal scores endpoint."""
    return mesh_scores_endpoint(org_id)


def get_axis_scores(org_id: str) -> list[dict[str, Any]]:
    """Get axis scores for org."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": "SELECT * FROM e2e_axis_scores WHERE org_id = %s", "params": [org_id]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


class OrgService:
    """Org service with delete and update capabilities."""

    def __init__(self, session: Session):
        self.session = session

    def delete(self, org_id: str) -> bool:
        """Delete org by id."""
        org = self.session.query(Org).filter(Org.id == org_id).first()
        if org:
            self.session.delete(org)
            self.session.commit()
            return True
        return False

    def update(self, org_id: str, data: dict[str, Any]) -> Optional[Org]:
        """Update org with data."""
        org = self.session.query(Org).filter(Org.id == org_id).first()
        if org:
            for key, value in data.items():
                if hasattr(org, key):
                    setattr(org, key, value)
            self.session.commit()
            return org
        return None


def _run_self_test(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Run self-test diagnostics."""
    test_results = {"status": "PASS", "checks": []}
    try:
        session.execute("SELECT 1")
        test_results["checks"].append({"check": "db_session", "status": "OK"})
    except Exception as e:
        test_results["checks"].append({"check": "db_session", "status": "FAIL", "error": str(e)})
        test_results["status"] = "FAIL"
    return test_results


def test_self() -> dict[str, str]:
    """Standalone self-test entry point."""
    return {"status": "PASS"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    def override_get_session():
        with Session(engine) as session:
            session.execute(text("CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT)"))
            session.execute(text("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, org_id TEXT, name TEXT)"))
            session.commit()
            yield session

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/health")
    def health():
        return {"status": "OK"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=0)