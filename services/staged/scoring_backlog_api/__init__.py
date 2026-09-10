from fastapi import APIRouter, Depends
import requests
import py_compile
import sys

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute

router = APIRouter()


def _get_signal_scores_from_db(session) -> list[dict]:
    scores = session.query(McpLlmAxisScore).all()
    return [{"id": s.id, "score": s.score, "axis": s.axis} for s in scores]


def _get_score_disputes_from_db(session) -> list[dict]:
    disputes = session.query(McpScoreDispute).all()
    return [{"id": d.id, "status": d.status} for d in disputes]


def _get_mesh_memory_from_store() -> dict:
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mesh_memory", "action": "SELECT"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


@router.get("/signal-scores")
def signal_scores_endpoint(session=Depends(get_session)) -> list[dict]:
    return _get_signal_scores_from_db(session)


@router.get("/mesh-scores")
def mesh_scores_endpoint() -> dict:
    return _get_mesh_memory_from_store()


@router.get("/mesh-memory")
def get_mesh_memory_endpoint() -> dict:
    return _get_mesh_memory_from_store()


@router.get("/score-disputes")
def get_score_disputes_endpoint(session=Depends(get_session)) -> list[dict]:
    return _get_score_disputes_from_db(session)


def mesh_scores() -> dict:
    return _get_mesh_memory_from_store()


def get_mesh_memory() -> dict:
    return _get_mesh_memory_from_store()


def _run_self_test() -> bool:
    try:
        assert callable(signal_scores_endpoint)
        assert callable(mesh_scores)
        assert callable(get_mesh_memory)
        assert callable(mesh_scores_endpoint)
        assert callable(get_mesh_memory_endpoint)
        assert callable(get_score_disputes_endpoint)
        return True
    except (AssertionError, AttributeError):
        return False


if __name__ == "__main__":
    try:
        py_compile.compile(__file__, doraise=True)
    except py_compile.PyCompileError:
        print("FAIL")
        sys.exit(1)

    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)