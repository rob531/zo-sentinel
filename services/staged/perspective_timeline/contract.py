from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from starless.testclient import TestClient

from app.db import get_session
from app.models import PerspectiveEvent, McpServerRegistry

router = APIRouter()


class TimelineEvent(BaseModel):
    id: int
    server_id: int
    change_type: str
    old_tier: str | None
    new_tier: str | None
    created_at: str
    server_name: str


class TimelineResponse(BaseModel):
    perspective_id: int
    events: list[TimelineEvent]


@router.get("/api/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
def get_perspective_timeline(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> TimelineResponse:
    sql = text("""
        SELECT
            pe.id,
            pe.server_id,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.created_at,
            msr.name AS server_name
        FROM perspective_events pe
        JOIN mcp_server_registry msr ON pe.server_id = msr.id
        WHERE pe.perspective_id = :perspective_id
        ORDER BY pe.created_at
    """)
    rows = session.execute(sql, {"perspective_id": perspective_id}).fetchall()
    events = [
        TimelineEvent(
            id=row.id,
            server_id=row.server_id,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            created_at=str(row.created_at),
            server_name=row.server_name,
        )
        for row in rows
    ]
    return TimelineResponse(perspective_id=perspective_id, events=events)


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, DateTime, text as sql_text
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata = MetaData()

    Table("perspectives", metadata,
          Column("id", Integer, primary_key=True),
          Column("name", String),
          Column("description", String),
          Column("org_id", Integer),
          Column("facet_filters", String),
          Column("created_by", Integer),
          Column("created_at", DateTime),
          Column("updated_at", DateTime))

    Table("perspective_events", metadata,
          Column("id", Integer, primary_key=True),
          Column("perspective_id", Integer),
          Column("server_id", Integer),
          Column("change_type", String),
          Column("old_tier", String),
          Column("new_tier", String),
          Column("seen", Integer),
          Column("created_at", DateTime))

    Table("mcp_server_registry", metadata,
          Column("id", Integer, primary_key=True),
          Column("name", String))

    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(sql_text("INSERT INTO perspectives (id, name, description, org_id, created_at, updated_at) VALUES (1, 'Persp1', 'Desc1', 1, '2024-01-01 00:00:00', '2024-01-01 00:00:00')"), {})
        conn.execute(sql_text("INSERT INTO perspectives (id, name, description, org_id, created_at, updated_at) VALUES (2, 'Persp2', 'Desc2', 1, '2024-01-01 00:00:00', '2024-01-01 00:00:00')"), {})
        conn.execute(sql_text("INSERT INTO mcp_server_registry (id, name) VALUES (1, 'ServerA')"), {})
        conn.execute(sql_text("INSERT INTO mcp_server_registry (id, name) VALUES (2, 'ServerB')"), {})
        conn.execute(sql_text("INSERT INTO perspective_events (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at) VALUES (1, 1, 1, 'tier_change', 'low', 'high', 1, '2024-01-01 10:00:00')"), {})
        conn.execute(sql_text("INSERT INTO perspective_events (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at) VALUES (2, 1, 2, 'escalation', 'medium', 'critical', 1, '2024-01-02 11:00:00')"), {})
        conn.execute(sql_text("INSERT INTO perspective_events (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at) VALUES (3, 1, 1, 'tier_change', 'high', 'medium', 1, '2024-01-03 12:00:00')"), {})

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/perspectives/1/timeline")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["events"]) >= 3, f"Expected >= 3 events, got {len(data['events'])}"

    print("PASS")
    sys.exit(0)