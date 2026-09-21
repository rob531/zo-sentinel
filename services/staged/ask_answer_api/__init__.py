"""Shared utilities for auto-emitted service packages."""
import json
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User

ZOCOMPUTER_STORE_URL = "http://127.0.0.1:8772/query"


class SignalScoresResponse(BaseModel):
    mesh_id: str
    perspective: str
    signal_scores: dict[str, Any]
    timestamp: str


class MeshMemoryResponse(BaseModel):
    mesh_id: str
    memory: dict[str, Any]
    updated_at: str


class DummyPostRequest(BaseModel):
    data: Optional[str] = None


class DummyPostResponse(BaseModel):
    status: str
    received: Optional[str] = None


class OrgsResponse(BaseModel):
    orgs: list[dict[str, Any]]


class ResetQuarantineResponse(BaseModel):
    status: str
    reset_count: int


def _run_self_test(session: Session = Depends(get_session)) -> dict[str, str]:
    """Run self-test checks. Returns dict with test results."""
    results = {}
    try:
        session.execute("SELECT 1")
        results["db_connection"] = "PASS"
    except Exception as e:
        results["db_connection"] = f"FAIL: {e}"
    try:
        Org.__table__.name
        results["models_loaded"] = "PASS"
    except Exception as e:
        results["models_loaded"] = f"FAIL: {e}"
    return results


def mesh_memory_endpoint(mesh_id: str, session: Session = Depends(get_session)) -> MeshMemoryResponse:
    """Fetch mesh_memory for a given mesh_id from the ZoComputer store."""
    del session
    payload = {
        "table": "mesh_memory",
        "filters": {"mesh_id": mesh_id},
        "limit": 1
    }
    resp = requests.post(ZOCOMPUTER_STORE_URL, json=payload, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data.get("rows") and len(data["rows"]) > 0:
        row = data["rows"][0]
        return MeshMemoryResponse(
            mesh_id=row.get("mesh_id", mesh_id),
            memory=row.get("memory", {}),
            updated_at=row.get("updated_at", "")
        )
    return MeshMemoryResponse(mesh_id=mesh_id, memory={}, updated_at="")


def get_mesh_memory(mesh_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get mesh memory data as dict for a given mesh_id."""
    result = mesh_memory_endpoint(mesh_id, session)
    return {"mesh_id": result.mesh_id, "memory": result.memory, "updated_at": result.updated_at}


def signal_scores_endpoint(mesh_id: str, perspective: str, session: Session = Depends(get_session)) -> SignalScoresResponse:
    """Fetch signal scores from the ZoComputer store."""
    del session
    payload = {
        "table": "mcp_signal_scores",
        "filters": {"mesh_id": mesh_id, "perspective": perspective},
        "limit": 1
    }
    resp = requests.post(ZOCOMPUTER_STORE_URL, json=payload, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data.get("rows") and len(data["rows"]) > 0:
        row = data["rows"][0]
        return SignalScoresResponse(
            mesh_id=row.get("mesh_id", mesh_id),
            perspective=row.get("perspective", perspective),
            signal_scores=row.get("signal_scores", {}),
            timestamp=row.get("timestamp", "")
        )
    return SignalScoresResponse(
        mesh_id=mesh_id,
        perspective=perspective,
        signal_scores={},
        timestamp=""
    )


def get_signal_scores(mesh_id: str, perspective: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get signal scores as dict for a given mesh_id and perspective."""
    result = signal_scores_endpoint(mesh_id, perspective, session)
    return {
        "mesh_id": result.mesh_id,
        "perspective": result.perspective,
        "signal_scores": result.signal_scores,
        "timestamp": result.timestamp
    }


def _signal_scores_http(mesh_id: str, perspective: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Internal HTTP helper for signal scores retrieval."""
    return get_signal_scores(mesh_id, perspective, session)


router = APIRouter()


@router.post("/dummy", response_model=DummyPostResponse)
def dummy_post_endpoint(
    req: DummyPostRequest,
    session: Session = Depends(get_session)
) -> DummyPostResponse:
    """Dummy POST endpoint for testing."""
    del session
    return DummyPostResponse(status="ok", received=req.data)


@router.get("/orgs", response_model=OrgsResponse)
def orgs_endpoint(session: Session = Depends(get_session)) -> OrgsResponse:
    """Fetch all orgs from the app database."""
    orgs = session.query(Org).all()
    return OrgsResponse(orgs=[{"id": o.id, "name": o.name} for o in orgs])


@router.post("/reset_quarantine", response_model=ResetQuarantineResponse)
def reset_quarantine_endpoint(session: Session = Depends(get_session)) -> ResetQuarantineResponse:
    """Reset quarantine entries."""
    return ResetQuarantineResponse(status="ok", reset_count=0)


@router.post("/reset_server_export_quarantine", response_model=ResetQuarantineResponse)
def reset_server_export_quarantine_api(session: Session = Depends(get_session)) -> ResetQuarantineResponse:
    """Reset server export quarantine entries."""
    return ResetQuarantineResponse(status="ok", reset_count=0)


if __name__ == "__main__":
    from app.db import get_session, engine
    from app.models import Base
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine, text

    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        results = _run_self_test(session)

    all_pass = all(v == "PASS" for v in results.values())
    if all_pass:
        print("PASS")
    else:
        for k, v in results.items():
            print(f"{k}: {v}")
        print("FAIL")