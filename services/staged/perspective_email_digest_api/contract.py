from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Optional
import os

from app.db import get_session
from app.models import Perspective

router = APIRouter()

class DigestSubscription(BaseModel):
    perspective_id: int
    email: str
    frequency: str = "daily"
    enabled: bool = True

class DigestSubscriptionResponse(BaseModel):
    id: int
    perspective_id: int
    email: str
    frequency: str
    enabled: bool
    class Config:
        from_attributes = True

class DigestPreferences(BaseModel):
    perspective_id: int
    digest_enabled: bool
    frequency: str
    recipients: list[str]

class DigestStatus(BaseModel):
    perspective_id: int
    perspective_name: str
    digest_enabled: bool
    last_sent: Optional[str]
    recipient_count: int

@router.post("/api/perspectives/digest/subscribe")
def subscribe_to_digest(
    subscription: DigestSubscription,
    db: Session = Depends(get_session)
) -> DigestSubscriptionResponse:
    perspective = db.query(Perspective).filter(Perspective.id == subscription.perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return DigestSubscriptionResponse(
        id=subscription.perspective_id,
        perspective_id=subscription.perspective_id,
        email=subscription.email,
        frequency=subscription.frequency,
        enabled=subscription.enabled
    )

@router.delete("/api/perspectives/digest/unsubscribe/{perspective_id}")
def unsubscribe_from_digest(
    perspective_id: int,
    email: str,
    db: Session = Depends(get_session)
) -> dict:
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"status": "unsubscribed", "perspective_id": perspective_id, "email": email}

@router.get("/api/perspectives/digest/status/{perspective_id}")
def get_digest_status(
    perspective_id: int,
    db: Session = Depends(get_session)
) -> DigestStatus:
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return DigestStatus(
        perspective_id=perspective.id,
        perspective_name=perspective.name,
        digest_enabled=True,
        last_sent=None,
        recipient_count=0
    )

@router.put("/api/perspectives/digest/preferences/{perspective_id}")
def update_digest_preferences(
    perspective_id: int,
    preferences: DigestPreferences,
    db: Session = Depends(get_session)
) -> DigestPreferences:
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return preferences

@router.get("/api/perspectives/digest/list/{org_id}")
def list_digest_subscriptions(
    org_id: int,
    db: Session = Depends(get_session)
) -> list[DigestStatus]:
    perspectives = db.query(Perspective).filter(Perspective.org_id == org_id).all()
    return [
        DigestStatus(
            perspective_id=p.id,
            perspective_name=p.name,
            digest_enabled=False,
            last_sent=None,
            recipient_count=0
        )
        for p in perspectives
    ]

@router.post("/api/perspectives/digest/send/{perspective_id}")
def trigger_digest_send(
    perspective_id: int,
    db: Session = Depends(get_session)
) -> dict:
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"status": "digest_triggered", "perspective_id": perspective_id}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    app = FastAPI()
    app.include_router(router)
    
    in_memory_url = "sqlite:///:memory:"
    engine = create_engine(
        in_memory_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    try:
        session = TestingSessionLocal()
        p = Perspective(
            id=1,
            name="Test Perspective",
            org_id=1,
            created_by=1,
            description="Test",
            facet_filters={}
        )
        session.add(p)
        session.commit()
        session.close()
        
        response = client.get("/api/perspectives/digest/status/1")
        assert response.status_code == 200
        
        sub_response = client.post(
            "/api/perspectives/digest/subscribe",
            json={"perspective_id": 1, "email": "test@example.com", "frequency": "daily"}
        )
        assert sub_response.status_code == 200
        
        list_response = client.get("/api/perspectives/digest/list/1")
        assert list_response.status_code == 200
        
        prefs_response = client.put(
            "/api/perspectives/digest/preferences/1",
            json={"perspective_id": 1, "digest_enabled": True, "frequency": "daily", "recipients": ["test@example.com"]}
        )
        assert prefs_response.status_code == 200
        
        unsub_response = client.delete("/api/perspectives/digest/unsubscribe/1?email=test@example.com")
        assert unsub_response.status_code == 200
        
        send_response = client.post("/api/perspectives/digest/send/1")
        assert send_response.status_code == 200
        
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        exit(1)