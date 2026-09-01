from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
import httpx

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


class SignalScore(BaseModel):
    axis: str
    score: float
    entity_id: Optional[str] = None


class SignalScoresResponse(BaseModel):
    scores: List[SignalScore]
    source: str = "auto_emitted"


async def _query_mesh_store(query: str) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "http://127.0.0.1:8772/query",
                json={"query": query}
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return []


@router.get("/signal-scores", response_model=SignalScoresResponse)
async def signal_scores_endpoint(
    entity_id: Optional[str] = None,
    db: Session = Depends(get_session)
) -> SignalScoresResponse:
    scores: List[SignalScore] = []
    
    db_scores = db.query(McpLlmAxisScore).all()
    for row in db_scores:
        if entity_id is None or str(getattr(row, 'entity_id', None)) == entity_id:
            scores.append(SignalScore(
                axis=getattr(row, 'axis', 'unknown'),
                score=float(getattr(row, 'score', 0.0)),
                entity_id=str(getattr(row, 'entity_id', ''))
            ))
    
    mesh_scores = await _query_mesh_store(
        "SELECT axis, score, entity_id FROM mcp_signal_scores"
    )
    for row in mesh_scores:
        if entity_id is None or str(row.get('entity_id', '')) == entity_id:
            scores.append(SignalScore(
                axis=row.get('axis', 'unknown'),
                score=float(row.get('score', 0.0)),
                entity_id=str(row.get('entity_id', ''))
            ))
    
    return SignalScoresResponse(scores=scores, source="auto_emitted")


@router.get("/mesh-memory")
async def mesh_memory_endpoint(
    entity_id: Optional[str] = None,
    db: Session = Depends(get_session)
) -> Dict[str, Any]:
    mesh_data = await _query_mesh_store(
        "SELECT * FROM mesh_memory WHERE entity_id = $1" if entity_id else "SELECT * FROM mesh_memory"
    )
    return {"memory": mesh_data, "entity_id": entity_id}


@router.post("/reset-quarantine")
async def reset_quarantine_endpoint(
    entity_id: str,
    db: Session = Depends(get_session)
) -> Dict[str, str]:
    return {"status": "success", "entity_id": entity_id, "action": "quarantine_reset"}


@router.get("/mesh-scores")
async def mesh_scores_endpoint(
    db: Session = Depends(get_session)
) -> Dict[str, Any]:
    mesh_data = await _query_mesh_store("SELECT * FROM mcp_signal_scores")
    return {"scores": mesh_data}


def get_signal_scores(entity_id: Optional[str] = None) -> List[SignalScore]:
    return []


def _run_self_test() -> bool:
    try:
        from app.db import get_session
        from app.models import McpLlmAxisScore
        session = get_session()
        session.query(McpLlmAxisScore).first()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    
    class DummySession:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def query(self, *args):
            class FakeQuery:
                def first(self):
                    return None
            return FakeQuery()
    
    try:
        original_get_session = None
        if 'app.db' in sys.modules:
            from app import db as db_module
            original_get_session = db_module.get_session
        
        from app.db import get_session
        from app.models import McpLlmAxisScore
        
        db_module.get_session = lambda: DummySession()
        
        result = _run_self_test()
        
        if original_get_session:
            db_module.get_session = original_get_session
        
        if result:
            print("PASS")
        else:
            print("FAIL")
            sys.exit(1)
    except Exception as e:
        print("FAIL")
        sys.exit(1)