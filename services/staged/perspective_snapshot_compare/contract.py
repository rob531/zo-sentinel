"""Perspective snapshot comparison service."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["perspectives"])


class CompareResponse(BaseModel):
    perspective_id: str
    earlier: str
    later: str
    added: list[str]
    removed: list[str]
    unchanged: int


def get_perspective_snapshots_by_time(
    perspective_id: str,
    timestamp: str,
    session: Session
) -> Optional[dict]:
    query = text("""
        SELECT id, membership, perspective_id, taken_at
        FROM perspective_snapshots
        WHERE perspective_id = :perspective_id
          AND taken_at <= :timestamp
        ORDER BY taken_at DESC
        LIMIT 1
    """)
    result = session.execute(query, {"perspective_id": perspective_id, "timestamp": timestamp})
    row = result.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "membership": row[1],
        "perspective_id": row[2],
        "taken_at": row[3]
    }


def compute_diff(earlier_membership: list, later_membership: list) -> tuple[list[str], list[str], int]:
    earlier_set = set(earlier_membership) if earlier_membership else set()
    later_set = set(later_membership) if later_membership else set()
    added = list(later_set - earlier_set)
    removed = list(earlier_set - later_set)
    unchanged = len(earlier_set & later_set)
    return added, removed, unchanged


@router.get("/perspectives/{perspective_id}/compare", response_model=CompareResponse)
def compare_perspectives(
    perspective_id: str,
    earlier: str = Query(..., description="Earlier timestamp"),
    later: str = Query(..., description="Later timestamp"),
    session: Session = Depends(get_session)
) -> CompareResponse:
    earlier_snapshot = get_perspective_snapshots_by_time(perspective_id, earlier, session)
    later_snapshot = get_perspective_snapshots_by_time(perspective_id, later, session)
    
    earlier_ts = earlier_snapshot["taken_at"] if earlier_snapshot else None
    later_ts = later_snapshot["taken_at"] if later_snapshot else None
    
    if earlier_snapshot and later_snapshot:
        import json
        earlier_membership = json.loads(earlier_snapshot["membership"]) if isinstance(earlier_snapshot["membership"], str) else earlier_snapshot["membership"]
        later_membership = json.loads(later_snapshot["membership"]) if isinstance(later_snapshot["membership"], str) else later_snapshot["membership"]
    else:
        earlier_membership = []
        later_membership = []
    
    added, removed, unchanged = compute_diff(earlier_membership or [], later_membership or [])
    
    return CompareResponse(
        perspective_id=perspective_id,
        earlier=str(earlier_ts) if earlier_ts else earlier,
        later=str(later_ts) if later_ts else later,
        added=added,
        removed=removed,
        unchanged=unchanged
    )


def write_service(query: str, params: dict = None):
    import requests
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE perspective_snapshots (
                id INTEGER PRIMARY KEY,
                perspective_id TEXT NOT NULL,
                membership TEXT NOT NULL,
                taken_at TIMESTAMP NOT NULL
            )
        """))

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        session.execute(text("""
            INSERT INTO perspective_snapshots (id, perspective_id, membership, taken_at)
            VALUES 
                (1, 'persp-001', '["server-a", "server-b", "server-c"]', '2024-01-01T00:00:00'),
                (2, 'persp-001', '["server-b", "server-c", "server-d", "server-e"]', '2024-01-15T00:00:00')
        """))
        session.commit()

    client = TestClient(test_app)

    response = client.get(
        "/api/perspectives/persp-001/compare",
        params={"earlier": "2024-01-05T00:00:00", "later": "2024-01-20T00:00:00"}
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert len(data["added"]) == 2, f"Expected 2 added, got {len(data['added'])}"
    assert len(data["removed"]) == 1, f"Expected 1 removed, got {len(data['removed'])}"
    assert set(data["added"]) == {"server-d", "server-e"}, f"Added mismatch: {data['added']}"
    assert data["removed"] == ["server-a"], f"Removed mismatch: {data['removed']}"
    assert data["unchanged"] == 2, f"Expected 2 unchanged, got {data['unchanged']}"

    print("PASS")