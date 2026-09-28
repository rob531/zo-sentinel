from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy import select

router = APIRouter()


class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime


class TimelineResponse(BaseModel):
    perspective_id: str
    events: List[PerspectiveEventResponse]


def fetch_perspective_events(perspective_id: str, write_service) -> List[dict]:
    payload = {
        "query": {
            "type": "select",
            "table": "perspective_events",
            "where": [["perspective_id", "=", perspective_id]],
            "order_by": [["created_at", "desc"]]
        }
    }
    resp = write_service.post("http://127.0.0.1:8772/query", json=payload, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "rows" in result:
        return result["rows"]
    return result if isinstance(result, list) else []


def fetch_server_names(server_ids: List[int], session) -> dict:
    if not server_ids:
        return {}
    stmt = select(MCPServerRegistry.id, MCPServerRegistry.name).where(
        MCPServerRegistry.id.in_(server_ids)
    )
    result = session.execute(stmt).fetchall()
    return {row[0]: row[1] for row in result}


@router.get("/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
def get_timeline(perspective_id: str, write_service, session=Depends(get_session)):
    events_raw = fetch_perspective_events(perspective_id, write_service)
    
    if not events_raw:
        return TimelineResponse(perspective_id=perspective_id, events=[])
    
    server_ids = [e["server_id"] for e in events_raw]
    server_names = fetch_server_names(server_ids, session)
    
    events = []
    for e in events_raw:
        sid = e["server_id"]
        events.append(PerspectiveEventResponse(
            id=e["id"],
            server_id=sid,
            server_name=server_names.get(sid, f"server_{sid}"),
            change_type=e["change_type"],
            old_tier=e.get("old_tier"),
            new_tier=e.get("new_tier"),
            seen=e.get("seen", False),
            created_at=e["created_at"]
        ))
    
    return TimelineResponse(perspective_id=perspective_id, events=events)


if __name__ == "__main__":
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.models as models

    mock_events = [
        {"id": 1, "server_id": 10, "change_type": "tier_change", "old_tier": "basic", "new_tier": "premium", "seen": False, "created_at": "2024-01-15T10:00:00"},
        {"id": 2, "server_id": 11, "change_type": "assigned", "old_tier": None, "new_tier": "basic", "seen": True, "created_at": "2024-01-14T09:00:00"}
    ]

    mock_write_service = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"rows": mock_events}
    mock_response.raise_for_status = MagicMock()
    mock_write_service.post.return_value = mock_response

    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()
    
    test_server = MCPServerRegistry(id=10, name="Test Server Alpha")
    test_session.add(test_server)
    test_session.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)
    response = client.get("/perspectives/test-p-1/timeline")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["perspective_id"] == "test-p-1"
    assert "events" in data
    assert len(data["events"]) >= 1, f"Expected at least 1 event, got {len(data['events'])}"
    
    print("PASS")