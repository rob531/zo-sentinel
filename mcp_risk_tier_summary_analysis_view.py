from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_server_registry
from typing import Dict
from collections import Counter

router = APIRouter()

@router.get("/risk-tier-summary", response_model=Dict[str, int])
def get_risk_tier_summary(session: Session = Depends(get_session)):
    servers = session.query(mcp_server_registry).all()
    tier_counts = Counter(server.risk_tier for server in servers)
    top_tiers = dict(tier_counts.most_common(5))
    return top_tiers

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_risk_tier_summary():
        db = TestingSessionLocal()
        try:
            for i in range(7):
                db.add(mcp_server_registry(risk_tier=f"tier_{i}", server_id=f"server_{i}"))
            db.commit()
        finally:
            db.close()

        response = client.get("/risk-tier-summary")
        assert response.status_code == 200
        assert len(response.json()) == 5
        print("PASS")

    test_risk_tier_summary()