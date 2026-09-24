from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from typing import List
from datetime import date

from app.db import get_session

router = APIRouter()


class TierChangeResponse(BaseModel):
    server_id: str
    old_tier: str
    new_tier: str
    change_date: str


def get_tier_changes(
    start_date: date,
    end_date: date,
    session,
) -> List[dict]:
    try:
        result = session.execute(
            text("""
                SELECT server_id, old_tier, new_tier, change_date
                FROM mcp_risk_timeline
                WHERE change_date >= :start_date
                  AND change_date <= :end_date
                  AND old_tier != new_tier
                ORDER BY change_date
            """),
            {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
        )
        rows = result.fetchall()
        return [
            {
                "server_id": row.server_id,
                "old_tier": row.old_tier,
                "new_tier": row.new_tier,
                "change_date": row.change_date,
            }
            for row in rows
        ]
    except Exception:
        return []


@router.get("/api/risk/tier-changes", response_model=List[TierChangeResponse])
def risk_tier_changes_dashboard_view(
    start_date: date,
    end_date: date,
    session=Depends(get_session),
):
    changes = get_tier_changes(start_date, end_date, session)
    return changes


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_risk_timeline (
                server_id TEXT,
                old_tier TEXT,
                new_tier TEXT,
                change_date TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO mcp_risk_timeline (server_id, old_tier, new_tier, change_date)
            VALUES
                ('server-a', 'low', 'medium', '2024-01-10'),
                ('server-b', 'low', 'high', '2024-01-12'),
                ('server-c', 'low', 'low', '2024-01-08')
        """))
        conn.commit()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/risk/tier-changes", params={"start_date": "2024-01-01", "end_date": "2024-01-20"})
    data = response.json()

    assert response.status_code == 200
    assert len(data) == 2
    assert data[0]["server_id"] == "server-a"
    assert data[0]["old_tier"] == "low"
    assert data[0]["new_tier"] == "medium"
    assert data[1]["server_id"] == "server-b"
    assert data[1]["old_tier"] == "low"
    assert data[1]["new_tier"] == "high"

    print("PASS")