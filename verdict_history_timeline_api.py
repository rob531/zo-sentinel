from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from app.db import get_session
from app.models import McpRiskRegister
from sqlalchemy.orm import Session
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class VerdictHistoryEntry(BaseModel):
    computed_at: str
    risk_tier: str
    overall_risk: float
    verdict: str
    confidence: Optional[float]

class VerdictHistoryResponse(BaseModel):
    server_id: str
    entries: List[VerdictHistoryEntry]
    total: int

def query_write_service(query: str, params: list) -> list:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying write service")
    return response.json()

@router.get("/verdicts/{server_id}/history", response_model=VerdictHistoryResponse)
async def get_verdict_history(
    server_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session)
):
    try:
        UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server_id format")

    query = """
        SELECT computed_at, risk_tier, overall_risk, verdict, confidence
        FROM mcp_risk_register
        WHERE server_id = %s
        ORDER BY computed_at ASC
        LIMIT %s OFFSET %s
    """
    params = [server_id, limit, offset]

    try:
        results = query_write_service(query, params)
    except HTTPException as e:
        raise e

    total_query = "SELECT COUNT(*) FROM mcp_risk_register WHERE server_id = %s"
    total_params = [server_id]
    total = query_write_service(total_query, total_params)[0]["count"]

    entries = [
        VerdictHistoryEntry(
            computed_at=entry["computed_at"].isoformat(),
            risk_tier=entry["risk_tier"],
            overall_risk=entry["overall_risk"],
            verdict=entry["verdict"],
            confidence=entry["confidence"]
        )
        for entry in results
    ]

    return VerdictHistoryResponse(
        server_id=server_id,
        entries=entries,
        total=total
    )

if __name__ == '__main__':
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_data = [
        {"server_id": "123e4567-e89b-12d3-a456-426614174000", "computed_at": datetime(2023, 1, 1), "risk_tier": "low", "overall_risk": 0.1, "verdict": "safe", "confidence": 0.9},
        {"server_id": "123e4567-e89b-12d3-a456-426614174000", "computed_at": datetime(2023, 1, 2), "risk_tier": "medium", "overall_risk": 0.5, "verdict": "monitor", "confidence": 0.8},
        {"server_id": "123e4567-e89b-12d3-a456-426614174000", "computed_at": datetime(2023, 1, 3), "risk_tier": "high", "overall_risk": 0.9, "verdict": "risky", "confidence": 0.7},
    ]

    with SessionLocal() as session:
        for data in test_data:
            session.add(McpRiskRegister(**data))
        session.commit()

    response = client.get("/verdicts/123e4567-e89b-12d3-a456-426614174000/history")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert len(data["entries"]) == 3
    assert data["total"] == 3
    for i in range(len(data["entries"])):
        assert data["entries"][i]["computed_at"] == test_data[i]["computed_at"].isoformat()
        assert data["entries"][i]["risk_tier"] == test_data[i]["risk_tier"]
        assert data["entries"][i]["overall_risk"] == test_data[i]["overall_risk"]
        assert data["entries"][i]["verdict"] == test_data[i]["verdict"]
        assert data["entries"][i]["confidence"] == test_data[i]["confidence"]

    print("PASS")