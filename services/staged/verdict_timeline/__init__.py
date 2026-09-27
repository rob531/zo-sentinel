from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select, func
import httpx


class ServerResponse(BaseModel):
    server_name: str
    server_id: str | None = None
    status: str = "ok"
    message: str | None = None

    class Config:
        from_attributes = True


class McpScoreDisputeService:
    def __init__(self, session=Depends(get_session)):
        self.session = session

    def get_open_disputes(self):
        stmt = select(McpScoreDispute).where(McpScoreDispute.resolved == False)
        result = self.session.execute(stmt).scalars().all()
        return result


class UserRead(BaseModel):
    user_id: int
    username: str
    email: str | None = None

    class Config:
        from_attributes = True


def signal_scores_endpoint(server_id: str, scores: dict) -> dict:
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "http://127.0.0.1:8772/write",
            json={"table": "mcp_signal_scores", "data": {"server_id": server_id, "scores": scores}},
        )
        return resp.json()


def mesh_memory_endpoint(server_id: str) -> dict:
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "filters": {"server_id": server_id}},
        )
        return resp.json()


def mesh_memory_endpoint_get(server_id: str) -> dict:
    return mesh_memory_endpoint(server_id)


def get_mesh_memory_endpoint(server_id: str) -> dict:
    return mesh_memory_endpoint(server_id)


def get_score_disputes_endpoint(dispute_id: str | None = None) -> list:
    with httpx.Client(timeout=10) as client:
        payload = {"table": "mcp_score_disputes"}
        if dispute_id:
            payload["filters"] = {"id": dispute_id}
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        return resp.json().get("rows", [])


def recency_report() -> dict:
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "limit": 100},
        )
        return resp.json()


def critical_risk_servers_endpoint() -> list[ServerResponse]:
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_server_registry", "filters": {"risk_tier": "critical"}},
        )
        rows = resp.json().get("rows", [])
        return [ServerResponse(**r) for r in rows]


def run_self_test() -> str:
    with httpx.Client(timeout=10) as client:
        try:
            resp = client.post("http://127.0.0.1:8772/health")
            if resp.status_code == 200:
                return "PASS"
        except Exception:
            pass
    return "FAIL"


def test_self() -> str:
    return run_self_test()


if __name__ == "__main__":
    print(run_self_test())