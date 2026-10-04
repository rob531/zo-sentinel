# deps: fastapi, pydantic, sqlalchemy, sqlmodel, passlib

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org, User, ApiKey

router = APIRouter()

class ServerScoreBacklogItem(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: datetime

class ServerScoreBacklogResponse(BaseModel):
    items: List[ServerScoreBacklogItem]
    total: int

@router.get("/", response_model=ServerScoreBacklogResponse)
def list_server_scores(
    org_id: str = Depends(get_org_id),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_session)
):
    scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.org_id == org_id).offset(skip).limit(limit).all()
    total = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.org_id == org_id).count()
    return {"items": scores, "total": total}

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_server_score(
    score: ServerScoreBacklogItem,
    org_id: str = Depends(get_org_id),
    db: Session = Depends(get_session)
):
    db_score = McpLlmAxisScore(**score.dict(), org_id=org_id)
    db.add(db_score)
    db.commit()
    db.refresh(db_score)
    return db_score

@router.get("/{server_id}/{axis_name}", response_model=ServerScoreBacklogItem)
def get_server_score(
    server_id: str,
    axis_name: str,
    org_id: str = Depends(get_org_id),
    db: Session = Depends(get_session)
):
    score = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.org_id == org_id
    ).first()
    if score is None:
        raise HTTPException(status_code=404, detail="Score not found")
    return score

@router.put("/{server_id}/{axis_name}", response_model=ServerScoreBacklogItem)
def update_server_score(
    server_id: str,
    axis_name: str,
    score_update: ServerScoreBacklogItem,
    org_id: str = Depends(get_org_id),
    db: Session = Depends(get_session)
):
    db_score = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.org_id == org_id
    ).first()
    if db_score is None:
        raise HTTPException(status_code=404, detail="Score not found")
    for key, value in score_update.dict().items():
        setattr(db_score, key, value)
    db.commit()
    db.refresh(db_score)
    return db_score

@router.delete("/{server_id}/{axis_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server_score(
    server_id: str,
    axis_name: str,
    org_id: str = Depends(get_org_id),
    db: Session = Depends(get_session)
):
    db_score = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.org_id == org_id
    ).first()
    if db_score is None:
        raise HTTPException(status_code=404, detail="Score not found")
    db.delete(db_score)
    db.commit()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test data
    test_score = {
        "server_id": "test_server",
        "axis_name": "overall_risk",
        "label": "HIGH",
        "label_index": 2,
        "probs": {"HIGH": 0.7, "MEDIUM": 0.2, "LOW": 0.1},
        "p_top": 0.7,
        "p_critical": 0.1,
        "p_danger": 0.2,
        "escalated": False,
        "escalated_to": None,
        "decision_rule_version": "1.0",
        "model_version": "1.0",
        "adapter_sha256": "abc123",
        "scored_at": datetime.now().isoformat()
    }

    # Test create
    response = client.post("/", json=test_score)
    assert response.status_code == 201

    # Test read
    response = client.get("/test_server/overall_risk")
    assert response.status_code == 200
    assert response.json()["label"] == "HIGH"

    # Test update
    updated_score = test_score.copy()
    updated_score["label"] = "MEDIUM"
    response = client.put("/test_server/overall_risk", json=updated_score)
    assert response.status_code == 200
    assert response.json()["label"] == "MEDIUM"

    # Test delete
    response = client.delete("/test_server/overall_risk")
    assert response.status_code == 204

    # Test not found
    response = client.get("/test_server/overall_risk")
    assert response.status_code == 404

    print("PASS")