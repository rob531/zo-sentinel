from typing import Any
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ComparisonData(BaseModel):
    server_id: int
    risk_tier: str
    comparison_data: dict[str, Any]


class ComparisonResponse(BaseModel):
    data: list[ComparisonData]


def risk_tier_comparison(session: Session = Depends(get_session)) -> list[ComparisonData]:
    query = text("""
        SELECT 
            server_id,
            risk_tier,
            json_object(
                'total_count', COUNT(*),
                'risk_tier', risk_tier
            ) as comparison_data
        FROM mcp_risk_register
        WHERE risk_tier IS NOT NULL
          AND server_id IS NOT NULL
        GROUP BY server_id, risk_tier
        ORDER BY server_id, risk_tier
    """)
    result = session.execute(query)
    rows = result.fetchall()
    return [
        ComparisonData(
            server_id=row[0],
            risk_tier=row[1],
            comparison_data={"risk_tier": row[1], "server_id": row[0]}
        )
        for row in rows
    ]


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_api_route("/api/risk-tier/comparison", risk_tier_comparison, methods=["GET"])
    return app


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_risk_register (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                risk_tier TEXT NOT NULL,
                risk_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO mcp_risk_register (server_id, risk_tier, risk_score) VALUES
            (1, 'high', 85.0),
            (1, 'high', 90.0),
            (2, 'medium', 55.0),
            (2, 'medium', 60.0),
            (3, 'low', 25.0),
            (3, 'low', 30.0)
        """))

    def override_get_session() -> Session:
        return SessionLocal()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/risk-tier/comparison")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data) == 3, f"Expected 3 entries, got {len(data)}"

    server_ids = {item["server_id"] for item in data}
    assert 1 in server_ids, f"Expected server_id 1 in results, got {server_ids}"

    print("PASS")