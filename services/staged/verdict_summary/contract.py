from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Dict

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

class VerdictSummaryResponse(BaseModel):
    total_servers: int
    by_tier: Dict[str, int]

@router.get("/verdicts/summary", response_model=VerdictSummaryResponse)
def get_verdict_summary(session: Session = Depends(get_session)):
    try:
        results = session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count")
        ).group_by(
            McpServerRegistry.risk_tier
        ).all()

        by_tier = {tier: count for tier, count in results}
        total_servers = sum(by_tier.values())

        return VerdictSummaryResponse(
            total_servers=total_servers,
            by_tier=by_tier
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_get_session():
        conn = engine.connect()
        session = Session(bind=conn, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()
            conn.close()

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    with engine.connect() as conn:
        conn.execute(
            McpServerRegistry.__table__.insert(),
            [
                {"server_id": 1, "risk_tier": "TRUSTED_GENERAL"},
                {"server_id": 2, "risk_tier": "TRUSTED_RESEARCH"},
                {"server_id": 3, "risk_tier": "ENTERPRISE_CONTROLLED"},
            ]
        )
        conn.commit()

    response = client.get("/api/verdicts/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] >= 3
    for tier in data["by_tier"].values():
        assert tier >= 0

    print("PASS")