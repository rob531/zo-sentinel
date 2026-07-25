from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List
from app.db import get_session
from app.models import User, RiskRegister
import smtplib
from email.mime.text import MIMEText
from fastapi.testclient import TestClient

router = APIRouter()

class RiskTierChange(BaseModel):
    id: int
    old_tier: str
    new_tier: str
    changed_at: str
    organization_id: int

class EmailConfig(BaseModel):
    smtp_server: str
    smtp_port: int
    sender_email: str
    sender_password: str

email_config = EmailConfig(
    smtp_server="smtp.example.com",
    smtp_port=587,
    sender_email="alerts@zo-sentinel.com",
    sender_password="password"
)

def send_email(to: str, subject: str, body: str):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = email_config.sender_email
    msg['To'] = to

    with smtplib.SMTP(email_config.smtp_server, email_config.smtp_port) as server:
        server.starttls()
        server.login(email_config.sender_email, email_config.sender_password)
        server.send_message(msg)

@router.get("/alerts/risk-tier-changes", response_model=List[RiskTierChange])
async def get_risk_tier_changes(db: Session = Depends(get_session)):
    changes = db.query(
        RiskRegister.id,
        RiskRegister.old_tier,
        RiskRegister.new_tier,
        RiskRegister.changed_at,
        RiskRegister.organization_id
    ).filter(
        RiskRegister.old_tier != RiskRegister.new_tier
    ).all()

    if not changes:
        raise HTTPException(status_code=404, detail="No risk tier changes found")

    subscribers = db.query(User.email).all()
    for subscriber in subscribers:
        body = f"Risk tier changes detected:\n\n"
        for change in changes:
            body += f"Organization ID: {change.organization_id}\n"
            body += f"Old Tier: {change.old_tier}\n"
            body += f"New Tier: {change.new_tier}\n"
            body += f"Changed At: {change.changed_at}\n\n"
        send_email(subscriber.email, "Risk Tier Change Alert", body)

    return changes

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_db)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_db)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    response = client.get("/alerts/risk-tier-changes")
    assert response.status_code == 200
    print("PASS")