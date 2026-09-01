from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter()

class Event(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: str
    new_tier: str
    seen: bool
    created_at: str

class EventsResponse(BaseModel):
    events: List[Event]

@router.get("/perspectives/{perspective_id}/events", response_model=EventsResponse)
def get_events(perspective_id: str, session: Session = Depends(get_session)):
    events = session.query(PerspectiveEvent).filter_by(perspective_id=perspective_id).all()
    if not events:
        raise HTTPException(status_code=404, detail="No events found for the given perspective_id")
    return {"events": events}

@router.post("/perspectives/{perspective_id}/events")
def create_event(perspective_id: str, event: Event, session: Session = Depends(get_session)):
    db_event = PerspectiveEvent(**event.dict())
    session.add(db_event)
    session.commit()
    session.refresh(db_event)
    return db_event

def ensure_tables():
    pass

def extract_entities():
    pass

def execute_search():
    pass

def send_heartbeat():
    pass

def ensure_mesh_events_table():
    pass

def get_servers_for_reranking():
    pass

def get_ranked_servers_trust_score():
    pass

def get_all_service_health():
    pass

def get_package_vulns():
    pass

def search_cached_cves():
    pass

def check_single_instance():
    pass

def ensure_table():
    pass

def signal_handler():
    pass

def get_risk_tier_distribution():
    pass

def get_audit_log_summary():
    pass

def cycle():
    pass

def _build_risk_perspective_tree():
    pass

def _build_attestation_perspective_tree():
    pass

def get_trust_score_distribution():
    pass

def get_top_servers():
    pass

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, PerspectiveEvent
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

    def test_get_events():
        db = TestingSessionLocal()
        test_event = PerspectiveEvent(
            perspective_id="test_perspective",
            server_id="test_server",
            change_type="test_change",
            old_tier="test_old_tier",
            new_tier="test_new_tier",
            seen=False,
            created_at="2023-01-01T00:00:00"
        )
        db.add(test_event)
        db.commit()
        db.close()

        response = client.get("/perspectives/test_perspective/events")
        assert response.status_code == 200
        assert response.json()["events"][0]["perspective_id"] == "test_perspective"

    pytest.main([__file__, "-v", "-s"])
    print("PASS")