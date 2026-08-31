from datetime import date, datetime
from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


class TimelineEntry(BaseModel):
    date: str
    tier: str
    count: int


class TimelineResponse(BaseModel):
    series: List[TimelineEntry]


def get_timeline_data(session: Session) -> Dict[str, Any]:
    query = text("""
        SELECT 
            DATE(last_assessed) as assessed_date,
            risk_tier,
            COUNT(*) as count
        FROM McpServerRegistry
        WHERE last_assessed IS NOT NULL
          AND risk_tier IS NOT NULL
        GROUP BY DATE(last_assessed), risk_tier
        ORDER BY assessed_date, risk_tier
    """)
    result = session.execute(query)
    rows = result.fetchall()
    
    series = []
    for row in rows:
        assessed_date = row[0]
        if isinstance(assessed_date, str):
            date_str = assessed_date
        elif isinstance(assessed_date, datetime):
            date_str = assessed_date.date().isoformat()
        else:
            date_str = str(assessed_date)
        series.append({
            "date": date_str,
            "tier": row[1],
            "count": row[2]
        })
    
    return {"series": series}


@router.get("/api/verdict/distribution-timeline", response_model=TimelineResponse)
def get_distribution_timeline(session: Session = Depends(get_session)):
    return get_timeline_data(session)


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    memory_db = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with memory_db.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                last_seen TIMESTAMP,
                first_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.commit()

        today = date.today().isoformat()
        yesterday = (datetime.now().replace(hour=0, minute=0, second=0).date()).isoformat()
        
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
            VALUES ('srv-001', 'Server One', 'high', :date1)
        """), {"date1": today})
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
            VALUES ('srv-002', 'Server Two', 'medium', :date1)
        """), {"date1": today})
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
            VALUES ('srv-003', 'Server Three', 'high', :date2)
        """), {"date2": yesterday})
        conn.commit()

    TestingSessionLocal = sessionmaker(bind=memory_db)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(that_app)

    response = client.get("/api/verdict/distribution-timeline")
    
    if response.status_code == 200:
        data = response.json()
        series = data.get("series", [])
        if len(series) >= 2:
            print("PASS")
        else:
            print("FAIL: series length < 2")
    else:
        print("FAIL: status", response.status_code)