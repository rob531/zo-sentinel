"""GET /perspectives/{perspective_id}/timeline -- a perspective's change events.

GR-29 (2026-11-27): rewritten DB-direct. perspective_events is a Postgres table
(app.models.PerspectiveEvent); the previous version POSTed a query for it to the
factory write_service on 127.0.0.1:8772, which is not in the Fly image, and took an
un-injectable `write_service` handler argument that FastAPI read as a REQUIRED query
parameter -- so the route answered 422 without it and 500 with it.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter()


class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: str
    server_name: str
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: Optional[datetime] = None


class TimelineResponse(BaseModel):
    perspective_id: str
    events: List[PerspectiveEventResponse]


def fetch_server_names(server_ids: List[str], session: Session) -> dict:
    if not server_ids:
        return {}
    stmt = select(McpServerRegistry.server_id, McpServerRegistry.name).where(
        McpServerRegistry.server_id.in_(server_ids))
    return {row[0]: row[1] for row in session.execute(stmt).fetchall()}


@router.get("/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
def get_timeline(perspective_id: str, session: Session = Depends(get_session)):
    rows = session.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
    ).scalars().all()
    names = fetch_server_names(sorted({r.server_id for r in rows}), session)
    return TimelineResponse(
        perspective_id=perspective_id,
        events=[
            PerspectiveEventResponse(
                id=r.id,
                server_id=r.server_id,
                server_name=names.get(r.server_id) or f"server_{r.server_id}",
                change_type=r.change_type,
                old_tier=r.old_tier,
                new_tier=r.new_tier,
                seen=bool(r.seen),
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)
    s = TS()
    s.add(McpServerRegistry(server_id="10", name="Test Server Alpha"))
    s.add_all([
        PerspectiveEvent(perspective_id="p1", server_id="10", change_type="tier_changed",
                         old_tier="basic", new_tier="premium", seen=False,
                         created_at=datetime(2024, 1, 15, 10)),
        PerspectiveEvent(perspective_id="p1", server_id="11", change_type="entered",
                         new_tier="basic", seen=True, created_at=datetime(2024, 1, 14, 9)),
        PerspectiveEvent(perspective_id="p2", server_id="10", change_type="left",
                         created_at=datetime(2024, 1, 16)),
    ])
    s.commit(); s.close()

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    r = c.get("/perspectives/p1/timeline")
    assert r.status_code == 200, r.text
    ev = r.json()["events"]
    assert [e["server_id"] for e in ev] == ["10", "11"], ev
    assert ev[0]["server_name"] == "Test Server Alpha" and ev[1]["server_name"] == "server_11"
    assert c.get("/perspectives/none/timeline").json()["events"] == []
    print("PASS")
