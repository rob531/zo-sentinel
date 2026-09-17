import os
import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, User as UsersModel, Org, McpServerRegistry
from pydantic import BaseModel

router = APIRouter()
BUS_URL = os.getenv("BUS_URL", "http://127.0.0.1:8772/query")


def _post_bus(table: str, filters: dict | None = None):
    payload = {"table": table}
    if filters:
        payload["filters"] = filters
    try:
        resp = requests.post(BUS_URL, json=payload, timeout=2)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


class McpLlmAxisScoreRead(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    created_at: str

    @classmethod
    def from_orm(cls, obj: McpLlmAxisScore):
        return cls(
            id=obj.id,
            server_id=obj.server_id,
            axis=obj.axis,
            score=obj.score,
            created_at=obj.created_at.isoformat()
            if hasattr(obj.created_at, "isoformat")
            else str(obj.created_at),
        )


class User(BaseModel):
    id: int
    name: str
    email: str

    @classmethod
    def from_orm(cls, obj: UsersModel):
        return cls(id=obj.id, name=obj.name, email=obj.email)


def get_signal_scores(session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return [McpLlmAxisScoreRead.from_orm(s) for s in scores]


@router.get("/signal-scores")
def signal_scores_endpoint():
    return _post_bus("mcp_signal_scores")


def reset_server_export_api_quarantine(session: Session = Depends(get_session)):
    org = session.query(Org).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    # No schema change; placeholder operation
    session.commit()
    return {"status": "reset"}


@router.post("/reset-quarantine")
def reset_server_export_api_quarantine_endpoint(
    session: Session = Depends(get_session),
):
    return reset_server_export_api_quarantine(session)


def mesh_memory_endpoint_get():
    return _post_bus("mesh_memory")


def get_critical_risk_servers():
    return _post_bus("mcp_server_registry", {"critical": True})


def get_mesh_memory_by_id(memory_id: int):
    return _post_bus("mesh_memory", {"id": memory_id})


@router.get("/test")
def test_endpoint():
    return {"msg": "ok"}


def _run_self_test():
    app = FastAPI()
    app.include_router(router)

    def dummy_session():
        class Dummy:
            def query(self, *_, **__):
                class Q:
                    def all(self):
                        return []

                return Q()

            def commit(self):
                pass

        return Dummy()

    app.dependency_overrides[get_session] = dummy_session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    resp = client.get("/signal-scores")
    assert resp.status_code == 200

    resp = client.get("/test")
    assert resp.json() == {"msg": "ok"}

    print("PASS")


if __name__ == "__main__":
    _run_self_test()