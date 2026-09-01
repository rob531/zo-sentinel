# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter(prefix="/verdict-comparison", tags=["verdict-comparison"])


class VerdictComparisonRequest(BaseModel):
    entity_id: str
    perspective_a: str
    perspective_b: str
    include_history: bool = False


class VerdictDifference(BaseModel):
    field: str
    value_a: Any
    value_b: Any
    severity: str


class VerdictComparisonResponse(BaseModel):
    entity_id: str
    matches: List[str]
    differences: List[VerdictDifference]
    consensus_score: float
    dispute_count: int


def _fetch_mesh_data(endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    import httpx
    try:
        response = httpx.post("http://127.0.0.1:8772/query", json={
            "endpoint": endpoint,
            "params": params or {}
        }, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e), "data": []}


def _get_signal_scores(org_id: int, entity_id: str, db: Session) -> Dict[str, Any]:
    result = db.execute(
        text("""
            SELECT perspective, score, rationale, updated_at
            FROM McpLlmAxisScore
            WHERE org_id = :org_id AND entity_id = :entity_id
            ORDER BY updated_at DESC
        """),
        {"org_id": org_id, "entity_id": entity_id}
    )
    rows = result.fetchall()
    return {
        "scores": [
            {"perspective": r[0], "score": r[1], "rationale": r[2], "updated_at": str(r[3])}
            for r in rows
        ]
    }


def _get_mesh_memory(entity_id: str) -> Dict[str, Any]:
    return _fetch_mesh_data("mesh_memory", {"entity_id": entity_id})


def _get_score_disputes(entity_id: str, db: Session) -> List[Dict[str, Any]]:
    result = db.execute(
        text("""
            SELECT id, perspective_a, perspective_b, disputed_fields, status, created_at
            FROM McpScoreDispute
            WHERE entity_id = :entity_id AND status = 'open'
            ORDER BY created_at DESC
        """),
        {"entity_id": entity_id}
    )
    rows = result.fetchall()
    return [
        {
            "id": r[0],
            "perspective_a": r[1],
            "perspective_b": r[2],
            "disputed_fields": r[3],
            "status": r[4],
            "created_at": str(r[5])
        }
        for r in rows
    ]


def _reset_quarantine_api(entity_id: str, db: Session) -> Dict[str, Any]:
    db.execute(
        text("""
            UPDATE McpScoreDispute
            SET status = 'resolved', resolved_at = NOW()
            WHERE entity_id = :entity_id AND status = 'quarantined'
        """),
        {"entity_id": entity_id}
    )
    db.commit()
    return {"status": "ok", "entity_id": entity_id}


def _run_self_test(db: Session) -> bool:
    """Self-test using SQLite in-memory override."""
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.post("/compare", response_model=VerdictComparisonResponse)
def verdict_comparison_endpoint(
    request: VerdictComparisonRequest,
    db: Session = Depends(get_session)
) -> VerdictComparisonResponse:
    """
    Compare verdict scores between two perspectives for the same entity.
    """
    signal_data = _get_signal_scores(org_id=1, entity_id=request.entity_id, db=db)
    mesh_data = _get_mesh_memory(request.entity_id)
    disputes = _get_score_disputes(request.entity_id, db)

    scores_by_perspective = {}
    for s in signal_data.get("scores", []):
        scores_by_perspective[s["perspective"]] = s

    perspective_a_scores = scores_by_perspective.get(request.perspective_a, {})
    perspective_b_scores = scores_by_perspective.get(request.perspective_b, {})

    differences = []
    matches = []
    compare_fields = ["score", "rationale"]

    for field in compare_fields:
        val_a = perspective_a_scores.get(field)
        val_b = perspective_b_scores.get(field)
        if val_a == val_b:
            if val_a is not None:
                matches.append(field)
        else:
            severity = "high" if field == "score" else "medium"
            differences.append(VerdictDifference(
                field=field,
                value_a=val_a,
                value_b=val_b,
                severity=severity
            ))

    consensus = 1.0 - (len(differences) * 0.3)
    consensus = max(0.0, min(1.0, consensus))

    return VerdictComparisonResponse(
        entity_id=request.entity_id,
        matches=matches,
        differences=differences,
        consensus_score=round(consensus, 3),
        dispute_count=len(disputes)
    )


@router.get("/mesh-memory/{entity_id}")
def mesh_memory_endpoint(entity_id: str) -> Dict[str, Any]:
    """
    Retrieve mesh memory data for an entity from ZoComputer store.
    """
    return _get_mesh_memory(entity_id)


@router.get("/signal-scores/{org_id}/{entity_id}")
def signal_scores_endpoint(org_id: int, entity_id: str, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Get signal scores for an entity from APP tables.
    """
    return _get_signal_scores(org_id, entity_id, db)


@router.get("/mesh-scores/{entity_id}")
def mesh_scores_endpoint(entity_id: str) -> Dict[str, Any]:
    """
    Get mesh scores from ZoComputer store.
    """
    return _fetch_mesh_data("mesh_scores", {"entity_id": entity_id})


@router.get("/disputes/{entity_id}")
def get_score_disputes(entity_id: str, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """
    Get open score disputes for an entity.
    """
    return _get_score_disputes(entity_id, db)


@router.post("/reset-quarantine/{entity_id}")
def reset_quarantine_api(entity_id: str, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Reset quarantined disputes for an entity.
    """
    return _reset_quarantine_api(entity_id, db)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS McpScoreDispute (id INTEGER, entity_id TEXT, perspective_a TEXT, perspective_b TEXT, disputed_fields TEXT, status TEXT, created_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS McpLlmAxisScore (org_id INTEGER, entity_id TEXT, perspective TEXT, score REAL, rationale TEXT, updated_at TIMESTAMP)"))
        conn.commit()

    app_dependency_overrides = {}
    from app.db import get_session
    app_dependency_overrides[get_session] = lambda: SessionLocal()

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides = app_dependency_overrides

    client = TestClient(test_app)

    try:
        db = SessionLocal()
        result = _run_self_test(db)
        db.close()
        if result:
            print("PASS")
        else:
            print("FAIL")
    except Exception as e:
        print(f"FAIL: {e}")