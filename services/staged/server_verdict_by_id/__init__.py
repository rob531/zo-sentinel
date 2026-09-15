from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime
from app.db import get_session
from app.models import User, Org, McpServerRegistry, McpLlmAxisScore, McpScoreDispute
import httpx
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

router = APIRouter()


class TargetServer(BaseModel):
    server_id: str
    name: str
    org_id: int


class ServerResponse(BaseModel):
    status: str
    data: Optional[Any] = None
    error: Optional[str] = None


class PerspectiveSnapshot(BaseModel):
    perspective_id: str
    timestamp: datetime
    scores: Dict[str, float]


class UserRead(BaseModel):
    id: int
    email: str
    org_id: int


def mesh_memory_endpoint(key: Optional[str] = None) -> List[Dict[str, Any]]:
    with httpx.Client(timeout=10.0) as client:
        payload = {"table": "mesh_memory"}
        if key:
            payload["filters"] = {"key": key}
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        return resp.json().get("rows", [])


def mesh_memory_endpoint_get(key: Optional[str] = None) -> List[Dict[str, Any]]:
    return mesh_memory_endpoint(key=key)


def signal_scores_endpoint(server_id: str, score_type: str, score_value: float) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        payload = {
            "table": "mcp_signal_scores",
            "rows": [{"server_id": server_id, "score_type": score_type, "score_value": score_value}],
        }
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        return resp.json()


def get_score_disputes_endpoint() -> Dict[str, List[Dict[str, Any]]]:
    session = next(get_session())
    disputes = session.query(McpScoreDispute).filter(McpScoreDispute.resolved == False).all()
    return {"disputes": [{"id": d.id, "server_id": d.server_id, "reason": d.reason} for d in disputes]}


def recency_report(period_seconds: int = 3600) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        payload = {
            "table": "mcp_signal_scores",
            "filters": {"recency_seconds": period_seconds},
        }
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        servers = list(set(r.get("server_id") for r in rows if r.get("server_id")))
        return {"servers": servers, "period_seconds": period_seconds, "scores": rows}


@router.get("/mesh-memory")
def _mesh_memory(key: Optional[str] = None) -> List[Dict[str, Any]]:
    return mesh_memory_endpoint(key=key)


@router.post("/signal-scores")
def _signal_scores(server_id: str, score_type: str, score_value: float) -> Dict[str, Any]:
    return signal_scores_endpoint(server_id, score_type, score_value)


@router.get("/score-disputes")
def _score_disputes() -> Dict[str, List[Dict[str, Any]]]:
    return get_score_disputes_endpoint()


@router.get("/recency-report")
def _recency_report(period_seconds: int = 3600) -> Dict[str, Any]:
    return recency_report(period_seconds)


def run_self_test() -> bool:
    test_db = {"users": [{"id": 1, "email": "test@example.com", "org_id": 1}], "orgs": [{"id": 1, "name": "Test Org"}]}
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: test_db
    client = TestClient(that_app)
    r1 = client.get("/mesh-memory")
    assert r1.status_code == 200
    r2 = client.post("/signal-scores", json={"server_id": "s1", "score_type": "t1", "score_value": 0.5})
    assert r2.status_code == 200
    r3 = client.get("/recency-report")
    assert r3.status_code == 200
    data = r3.json()
    assert "servers" in data and "period_seconds" in data and "scores" in data
    print("PASS")
    return True


__all__ = [
    "mesh_memory_endpoint",
    "mesh_memory_endpoint_get",
    "signal_scores_endpoint",
    "get_score_disputes_endpoint",
    "recency_report",
    "TargetServer",
    "ServerResponse",
    "PerspectiveSnapshot",
    "UserRead",
    "router",
    "run_self_test",
]


if __name__ == "__main__":
    run_self_test()