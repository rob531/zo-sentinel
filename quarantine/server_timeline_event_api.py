from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, VulnLinks
from sqlalchemy import func, desc, or_
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class Event(BaseModel):
    timestamp: datetime
    source: str
    change_type: Optional[str] = None
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    axis_name: Optional[str] = None
    detail: Optional[str] = None

class ServerTimelineResponse(BaseModel):
    server_id: str
    events: List[Event]

def get_server_metadata(session, server_id: str):
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return {
        "server_id": server.server_id,
        "name": server.name,
        "risk_tier": server.risk_tier,
        "verdict": server.verdict,
        "trust_score": server.trust_score,
        "last_assessed": server.last_assessed
    }

def get_perspective_events(server_id: str):
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": """
                SELECT change_type, old_tier, new_tier, seen, created_at
                FROM perspective_event
                WHERE server_id = :server_id
                ORDER BY created_at DESC
                LIMIT 50
            """,
            "params": {"server_id": server_id}
        }
    )
    if response.status_code != 200:
        return []
    return response.json()

def get_risk_tier_history(server_id: str):
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": """
                SELECT risk_tier, created_at
                FROM perspective_snapshots
                WHERE server_id = :server_id
                ORDER BY created_at DESC
                LIMIT 50
            """,
            "params": {"server_id": server_id}
        }
    )
    if response.status_code != 200:
        return []
    return response.json()

def get_disputes(session, server_id: str):
    disputes = session.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id == server_id).all()
    return [
        {
            "status": dispute.status,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "reason_category": dispute.reason_category,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at
        }
        for dispute in disputes
    ]

def get_cve_exposure_count(session, server_id: str):
    cve_count = session.query(
        func.count(VulnLinks.advisory_id.distinct())
    ).filter(
        VulnLinks.server_id == server_id
    ).scalar()
    return cve_count

def get_axis_change_summary(session, server_id: str):
    latest_scores = session.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.score,
        MCPLLMAxisScores.scored_at
    ).filter(
        MCPLLMAxisScores.server_id == server_id
    ).order_by(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.scored_at.desc()
    ).all()

    axis_changes = {}
    for score in latest_scores:
        if score.axis_name not in axis_changes:
            axis_changes[score.axis_name] = {
                "old_score": None,
                "new_score": score.score,
                "scored_at": score.scored_at
            }
        else:
            if score.scored_at > axis_changes[score.axis_name]["scored_at"]:
                axis_changes[score.axis_name]["old_score"] = axis_changes[score.axis_name]["new_score"]
                axis_changes[score.axis_name]["new_score"] = score.score
                axis_changes[score.axis_name]["scored_at"] = score.scored_at

    return [
        {
            "axis_name": axis,
            "old_score": changes["old_score"],
            "new_score": changes["new_score"],
            "scored_at": changes["scored_at"]
        }
        for axis, changes in axis_changes.items()
    ]

@router.get("/servers/{server_id}/timeline", response_model=ServerTimelineResponse)
async def get_server_timeline(server_id: str, session=Depends(get_session)):
    try:
        server_metadata = get_server_metadata(session, server_id)
    except HTTPException as e:
        raise e

    perspective_events = get_perspective_events(server_id)
    risk_tier_history = get_risk_tier_history(server_id)
    disputes = get_disputes(session, server_id)
    cve_exposure_count = get_cve_exposure_count(session, server_id)
    axis_change_summary = get_axis_change_summary(session, server_id)

    events = []

    for event in perspective_events:
        events.append(Event(
            timestamp=event["created_at"],
            source="perspective_event",
            change_type=event["change_type"],
            old_tier=event["old_tier"],
            new_tier=event["new_tier"],
            detail=f"Seen: {event['seen']}"
        ))

    for history in risk_tier_history:
        events.append(Event(
            timestamp=history["created_at"],
            source="risk_tier_history",
            change_type="risk_tier_change",
            old_tier=None,
            new_tier=history["risk_tier"],
            detail=None
        ))

    for dispute in disputes:
        events.append(Event(
            timestamp=dispute["created_at"],
            source="dispute",
            change_type="dispute_" + dispute["status"],
            old_tier=None,
            new_tier=None,
            detail=f"Proposed risk: {dispute['proposed_overall_risk']}, Reason: {dispute['reason_category']}"
        ))

    for axis_change in axis_change_summary:
        events.append(Event(
            timestamp=axis_change["scored_at"],
            source="axis_change",
            change_type="axis_score_change",
            old_tier=None,
            new_tier=None,
            axis_name=axis_change["axis_name"],
            detail=f"Old score: {axis_change['old_score']}, New score: {axis_change['new_score']}"
        ))

    if cve_exposure_count > 0:
        events.append(Event(
            timestamp=datetime.now(),
            source="cve_exposure",
            change_type="cve_exposure_count",
            old_tier=None,
            new_tier=None,
            detail=f"CVE exposure count: {cve_exposure_count}"
        ))

    events.sort(key=lambda x: x.timestamp, reverse=True)
    events = events[:50]

    return ServerTimelineResponse(
        server_id=server_metadata["server_id"],
        events=events
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_db)
    TestSession = sessionmaker(bind=test_db)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_server_id = "test_server_123"
    test_server = MCPServerRegistry(
        server_id=test_server_id,
        name="Test Server",
        risk_tier="medium",
        verdict="approved",
        trust_score=85,
        last_assessed=datetime.now()
    )

    with TestSession() as session:
        session.add(test_server)
        session.commit()

    client = TestClient(app)

    response = client.get(f"/servers/{test_server_id}/timeline")
    assert response.status_code == 200
    assert len(response.json()["events"]) > 0
    event_sources = {event["source"] for event in response.json()["events"]}
    assert len(event_sources) >= 3

    response = client.get("/servers/non_existent_server/timeline")
    assert response.status_code == 404

    print("PASS")