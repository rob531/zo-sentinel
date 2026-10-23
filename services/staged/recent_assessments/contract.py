from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.db import get_session
from app.models import McpServerRegistry
from typing import Optional
from datetime import datetime
from fastapi.testclient import TestClient

router = APIRouter()


class Assessment(BaseModel):
    server_id: str
    name: str
    url: str
    verdict: Optional[str] = None
    last_assessed: Optional[datetime] = None


class RecentAssessmentsResponse(BaseModel):
    assessments: list[Assessment]


def validate_limit(limit_str: Optional[str]) -> Optional[int]:
    if limit_str is None:
        return None
    try:
        limit = int(limit_str)
    except (ValueError, TypeError):
        raise ValueError("limit must be a valid integer")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")
    return limit


@router.get("/assessments/recent", response_model=RecentAssessmentsResponse)
def get_recent_assessments(
    limit: Optional[int] = Query(default=None, description="Maximum number of recent assessments to return"),
    session: Session = Depends(get_session)
) -> RecentAssessmentsResponse:
    query = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.verdict,
        McpServerRegistry.last_assessed
    )
    
    query = query.order_by(McpServerRegistry.last_assessed.desc().nullslast())
    
    if limit is not None:
        query = query.limit(limit)
    
    results = query.all()
    
    assessments = [
        Assessment(
            server_id=row.server_id,
            name=row.name,
            url=row.url,
            verdict=row.verdict,
            last_assessed=row.last_assessed
        )
        for row in results
    ]
    
    return RecentAssessmentsResponse(assessments=assessments)


if __name__ == "__main__":
    import sqlalchemy
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                verdict TEXT,
                last_assessed TIMESTAMP,
                confidence FLOAT
            )
        """))
        
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, url, verdict, last_assessed, confidence)
            VALUES 
                ('srv_001', 'Alpha Server', 'https://alpha.example.com', 'pass', '2024-01-03 12:00:00', 0.95),
                ('srv_002', 'Beta API', 'https://beta.example.com', 'fail', '2024-01-02 10:00:00', 0.85),
                ('srv_003', 'Gamma Service', 'https://gamma.example.com', 'pass', '2024-01-01 08:00:00', 0.90)
        """))
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    from main import app
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/assessments/recent?limit=3")
    data = response.json()
    
    assert response.status_code == 200
    assert len(data["assessments"]) == 3
    assert data["assessments"][0]["server_id"] == "srv_001"
    assert data["assessments"][1]["server_id"] == "srv_002"
    assert data["assessments"][2]["server_id"] == "srv_003"
    
    print("PASS")