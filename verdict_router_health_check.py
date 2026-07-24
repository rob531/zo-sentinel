from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Dict, List, Optional
import requests
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry, ServiceHealth

router = APIRouter()

def get_daemon_health() -> List[Dict[str, str]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT name, status FROM service_health WHERE name IN ('inference_router', 'trust_synthesiser')"}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return []

def get_last_scored_at(session: Session) -> Optional[datetime]:
    last_score = session.query(MCPLLMAxisScores.timestamp).order_by(MCPLLMAxisScores.timestamp.desc()).first()
    return last_score[0] if last_score else None

@router.get("/health/verdict")
async def health_check(
    session: Session = Depends(get_session)
) -> Dict:
    checks = {
        "axis_scores_count": session.query(MCPLLMAxisScores).count(),
        "registry_count": session.query(MCPServerRegistry).count(),
        "last_scored_at": get_last_scored_at(session),
        "daemon_health": get_daemon_health()
    }

    status = "healthy"
    if checks["axis_scores_count"] == 0 or checks["registry_count"] == 0:
        status = "unhealthy"
    elif not checks["last_scored_at"] or any(
        daemon["status"] != "healthy" for daemon in checks["daemon_health"]
    ):
        status = "degraded"

    return {
        "status": status,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    response = client.get("/health/verdict")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "checks" in data and len(data["checks"]) > 0

    print("PASS")