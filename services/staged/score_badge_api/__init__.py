"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore, McpScoreDispute

SERVICE_NAME = "auto_emitted_service"
SERVICE_VERSION = "1.0.0"
MESH_QUERY_URL = "http://127.0.0.1:8772/query"

router = APIRouter()
app: FastAPI | None = None


def get_app() -> FastAPI:
    """Get or create the FastAPI app instance."""
    global app
    if app is None:
        app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)
        app.include_router(router)
    return app


def _query_mesh(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Query mesh/pipeline tables via ZoComputer store."""
    try:
        resp = requests.post(MESH_QUERY_URL, json=query, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except Exception:
        return []


def get_mesh_memory_by_id(mesh_memory_id: str) -> Optional[Dict[str, Any]]:
    """Get mesh memory by ID from ZoComputer store."""
    results = _query_mesh({
        "table": "mesh_memory",
        "filters": {"id": mesh_memory_id},
        "limit": 1
    })
    return results[0] if results else None


@router.get("/mesh_memory")
def mesh_memory_endpoint(
    session: Session = Depends(get_session),
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get mesh memory data from ZoComputer store."""
    return _query_mesh({
        "table": "mesh_memory",
        "limit": limit
    })


@router.get("/users")
def users_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get users endpoint."""
    return [
        {"id": u.id, "email": u.email, "org_id": u.org_id}
        for u in session.query(McpServerRegistry).limit(10).all()
    ]


@router.get("/score_disputes")
def score_disputes_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get score disputes endpoint."""
    return [
        {"id": d.id, "status": d.status, "dispute_reason": getattr(d, 'dispute_reason', None)}
        for d in session.query(McpScoreDispute).limit(50).all()
    ]


def get_score_disputes(session: Session = Depends(get_session)) -> List[McpScoreDispute]:
    """Get score disputes."""
    return session.query(McpScoreDispute).all()


def get_server_registries(session: Session = Depends(get_session)) -> List[McpServerRegistry]:
    """Get server registries."""
    return session.query(McpServerRegistry).all()


@router.get("/signal_scores")
def signal_scores_endpoint(
    response: Response,
    session: Session = Depends(get_session)
) -> List[Dict[str, Any]]:
    """Get signal scores from ZoComputer store."""
    return _query_mesh({
        "table": "mcp_signal_scores",
        "limit": 100
    })


def test_self(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Run self-test for this module."""
    return run_self_test()


def run_self_test() -> Dict[str, Any]:
    """Run self-test for the service."""
    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(test_app, raise_server_exceptions=False)

    tests_passed = True
    errors = []

    try:
        resp = client.get("/mesh_memory")
        if resp.status_code != 200:
            tests_passed = False
            errors.append(f"mesh_memory: {resp.status_code}")
    except Exception as e:
        tests_passed = False
        errors.append(f"mesh_memory: {e}")

    try:
        resp = client.get("/users")
        if resp.status_code != 200:
            tests_passed = False
            errors.append(f"users: {resp.status_code}")
    except Exception as e:
        tests_passed = False
        errors.append(f"users: {e}")

    try:
        resp = client.get("/signal_scores")
        if resp.status_code != 200:
            tests_passed = False
            errors.append(f"signal_scores: {resp.status_code}")
    except Exception as e:
        tests_passed = False
        errors.append(f"signal_scores: {e}")

    try:
        resp = client.get("/score_disputes")
        if resp.status_code != 200:
            tests_passed = False
            errors.append(f"score_disputes: {resp.status_code}")
    except Exception as e:
        tests_passed = False
        errors.append(f"score_disputes: {e}")

    if tests_passed:
        print("PASS")
        return {"status": "pass", "tests": 4}
    else:
        print(f"FAIL: {errors}")
        return {"status": "fail", "errors": errors}


def test_service_package() -> Dict[str, Any]:
    """Alias for run_self_test."""
    return run_self_test()


if __name__ == "__main__":
    run_self_test()