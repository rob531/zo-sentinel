from typing import Optional, List, Dict, Any
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


app = FastAPI()


class RiskTierChangeNotification(BaseModel):
    server_id: str
    server_name: str
    previous_tier: Optional[str]
    new_tier: str
    change_detected_at: datetime
    recipients: List[str]


class NotificationQueue:
    def __init__(self):
        self.queue: List[RiskTierChangeNotification] = []

    def add_notification(self, notification: RiskTierChangeNotification):
        self.queue.append(notification)

    def get_queue(self) -> List[RiskTierChangeNotification]:
        return self.queue

    def clear(self):
        self.queue = []


notification_queue = NotificationQueue()


SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@example.com")


def get_previous_risk_tier(session: Session, server_id: str) -> Optional[str]:
    result = session.execute(
        select(McpLlmAxisScore.scored_at)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(desc(McpLlmAxisScore.scored_at))
    )
    dates = result.fetchall()
    if not dates:
        return None
    
    latest_date = dates[0][0]
    
    server_result = session.execute(
        select(McpServerRegistry.risk_tier)
        .where(McpServerRegistry.server_id == server_id)
        .where(McpServerRegistry.last_assessed < latest_date)
        .order_by(desc(McpServerRegistry.last_assessed))
    )
    prev_tier = server_result.first()
    
    if prev_tier:
        return prev_tier[0]
    
    all_servers = session.execute(
        select(McpServerRegistry.risk_tier, McpServerRegistry.last_assessed)
        .where(McpServerRegistry.server_id == server_id)
    )
    all_records = all_servers.fetchall()
    
    if len(all_records) > 1:
        sorted_records = sorted(all_records, key=lambda x: x[1], reverse=True)
        return sorted_records[1][0] if sorted_records[1][0] else None
    
    return None


def get_server_stakeholders(session: Session, server_id: str) -> List[str]:
    result = session.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server or not server.meta:
        return []
    
    meta = server.meta if isinstance(server.meta, dict) else {}
    stakeholders = meta.get("stakeholder_emails", [])
    if not isinstance(stakeholders, list):
        stakeholders = [s.strip() for s in str(stakeholders).split(",") if s.strip()]
    
    if not stakeholders:
        stakeholders = ["security-team@example.com"]
    
    return stakeholders


def send_tier_change_email(notification: RiskTierChangeNotification):
    recipients = notification.recipients
    if not recipients:
        return
    
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = f"Risk Tier Change Alert: {notification.server_name}"
    
    body = f"""
Risk Tier Change Detected

Server: {notification.server_name}
Server ID: {notification.server_id}
Previous Tier: {notification.previous_tier or 'N/A'}
New Tier: {notification.new_tier}
Time: {notification.change_detected_at.isoformat()}

Please review this change and take appropriate action if needed.
"""
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            if SMTP_USER and SMTP_PASSWORD:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception:
        pass


def check_and_notify_tier_change(session: Session, server_id: str) -> Optional[RiskTierChangeNotification]:
    result = session.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
    )
    server = result.scalar_one_or_none()
    
    if not server:
        return None
    
    current_tier = server.risk_tier
    if not current_tier:
        return None
    
    previous_tier = get_previous_risk_tier(session, server_id)
    
    if previous_tier and previous_tier == current_tier:
        return None
    
    if previous_tier is None and current_tier == "unknown":
        return None
    
    recipients = get_server_stakeholders(session, server_id)
    
    notification = RiskTierChangeNotification(
        server_id=server_id,
        server_name=server.name or server_id,
        previous_tier=previous_tier,
        new_tier=current_tier,
        change_detected_at=datetime.utcnow(),
        recipients=recipients
    )
    
    notification_queue.add_notification(notification)
    send_tier_change_email(notification)
    
    return notification


def get_risk_tier(session: Session, server_id: str) -> Optional[str]:
    result = session.execute(
        select(McpServerRegistry.risk_tier)
        .where(McpServerRegistry.server_id == server_id)
    )
    row = result.first()
    return row[0] if row else None


def get_server_registry(session: Session, server_id: str) -> Optional[Dict[str, Any]]:
    result = session.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        return None
    
    return {
        "server_id": server.server_id,
        "name": server.name,
        "url": server.url,
        "risk_tier": server.risk_tier,
        "trust_score": server.trust_score,
        "confidence": server.confidence,
        "registry_source": server.registry_source,
        "last_assessed": server.last_assessed,
        "last_seen": server.last_seen,
        "first_seen": server.first_seen,
    }


def get_llm_axis_scores(session: Session, server_id: str) -> List[Dict[str, Any]]:
    result = session.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(desc(McpLlmAxisScore.scored_at))
    )
    scores = result.scalars().all()
    
    return [
        {
            "id": s.id,
            "server_id": s.server_id,
            "axis_name": s.axis_name,
            "label": s.label,
            "probs": s.probs,
            "scored_at": s.scored_at,
            "model_version": s.model_version,
        }
        for s in scores
    ]


def get_tier_transition_history(session: Session, server_id: str) -> List[Dict[str, Any]]:
    result = session.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .order_by(desc(McpServerRegistry.last_assessed))
    )
    records = result.scalars().all()
    
    transitions = []
    prev_tier = None
    
    for record in records:
        if prev_tier is not None and record.risk_tier != prev_tier:
            transitions.append({
                "from_tier": prev_tier,
                "to_tier": record.risk_tier,
                "transition_date": record.last_assessed,
            })
        prev_tier = record.risk_tier
    
    return transitions


@app.post("/notify/tier-change/{server_id}")
def notify_tier_change(server_id: str, session: Session = Depends(get_session)):
    notification = check_and_notify_tier_change(session, server_id)
    if not notification:
        return {"status": "no_change", "message": "No tier change detected"}
    return {"status": "notified", "notification": notification.dict()}


@app.get("/notification/queue")
def get_notification_queue():
    return {"queue": [n.dict() for n in notification_queue.get_queue()]}


@app.post("/notification/queue/clear")
def clear_notification_queue():
    notification_queue.clear()
    return {"status": "cleared"}


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    test_db_path = ":memory:"
    test_engine = create_engine(
        f"sqlite:///{test_db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    
    that_app = FastAPI()
    
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    that_app.dependency_overrides[get_session] = override_get_session
    
    from main_router import router as main_router
    
    @that_app.post("/notify/tier-change/{server_id}")
    def notify_tier_change_test(server_id: str, session: Session = Depends(get_session)):
        result = check_and_notify_tier_change(session, server_id)
        if not result:
            return {"status": "no_change"}
        return {"status": "notified", "notification": result.dict()}
    
    @that_app.get("/notification/queue")
    def get_queue_test():
        return {"queue": [n.dict() for n in notification_queue.get_queue()]}
    
    @that_app.post("/notification/queue/clear")
    def clear_queue_test():
        notification_queue.clear()
        return {"status": "cleared"}
    
    notification_queue.clear()
    
    with TestSession() as session:
        from datetime import datetime, timedelta
        
        server = McpServerRegistry(
            server_id="test-server-001",
            name="Test Server Alpha",
            url="https://test-alpha.example.com",
            risk_tier="low",
            trust_score=0.85,
            confidence=0.90,
            registry_source="test",
            last_assessed=datetime.utcnow() - timedelta(days=2),
            last_seen=datetime.utcnow() - timedelta(days=1),
            first_seen=datetime.utcnow() - timedelta(days=30),
            meta={"stakeholder_emails": ["test@example.com"]},
        )
        session.add(server)
        session.commit()
    
    with TestSession() as session:
        result = check_and_notify_tier_change(session, "test-server-001")
        assert result is not None, "Expected notification for initial tier assignment"
        assert result.new_tier == "low"
        assert result.previous_tier is None or result.previous_tier != "low"
    
    with TestSession() as session:
        server = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == "test-server-001"
        ).first()
        server.risk_tier = "high"
        server.last_assessed = datetime.utcnow()
        session.commit()
    
    with TestSession() as session:
        result = check_and_notify_tier_change(session, "test-server-001")
        assert result is not None, "Expected notification for tier change"
        assert result.previous_tier == "low", f"Expected previous tier 'low', got {result.previous_tier}"
        assert result.new_tier == "high", f"Expected new tier 'high', got {result.new_tier}"
    
    with TestSession() as session:
        result = check_and_notify_tier_change(session, "test-server-001")
        assert result is None, "Should not notify when tier unchanged"
    
    queue = notification_queue.get_queue()
    assert len(queue) >= 2, f"Expected at least 2 notifications in queue, got {len(queue)}"
    
    print("PASS")
    sys.exit(0)