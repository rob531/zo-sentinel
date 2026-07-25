from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import RiskTierAlert
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

router = APIRouter()

class RiskTierAlertResponse(BaseModel):
    server_id: str
    alert_type: str
    alert_message: str
    alert_severity: str
    alert_timestamp: str

class PaginatedRiskTierAlerts(BaseModel):
    alerts: List[RiskTierAlertResponse]
    total: int
    page: int
    per_page: int

@router.get("/risk_tier_alerts", response_model=PaginatedRiskTierAlerts)
async def get_risk_tier_alerts(
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100)
):
    offset = (page - 1) * per_page
    total = session.query(RiskTierAlert).count()
    alerts = session.query(RiskTierAlert).offset(offset).limit(per_page).all()

    return {
        "alerts": [
            {
                "server_id": alert.server_id,
                "alert_type": alert.alert_type,
                "alert_message": alert.alert_message,
                "alert_severity": alert.alert_severity,
                "alert_timestamp": alert.alert_timestamp.isoformat()
            }
            for alert in alerts
        ],
        "total": total,
        "page": page,
        "per_page": per_page
    }

@router.post("/risk_tier_alerts")
async def create_risk_tier_alert(
    alert: RiskTierAlertResponse,
    session: Session = Depends(get_session)
):
    db_alert = RiskTierAlert(
        server_id=alert.server_id,
        alert_type=alert.alert_type,
        alert_message=alert.alert_message,
        alert_severity=alert.alert_severity,
        alert_timestamp=alert.alert_timestamp
    )
    session.add(db_alert)
    session.commit()
    session.refresh(db_alert)
    return db_alert

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    async def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Add test data
    with TestingSessionLocal() as session:
        test_alert = RiskTierAlert(
            server_id="test_server",
            alert_type="test_type",
            alert_message="test_message",
            alert_severity="test_severity",
            alert_timestamp="2023-01-01T00:00:00"
        )
        session.add(test_alert)
        session.commit()

    client = TestClient(app)
    response = client.get("/risk_tier_alerts")
    assert response.status_code == 200
    assert len(response.json()["alerts"]) == 1
    print("PASS")