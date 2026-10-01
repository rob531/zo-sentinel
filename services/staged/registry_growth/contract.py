from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel
from typing import List

app = FastAPI()

class GrowthData(BaseModel):
    date: str
    count: int

class GrowthResponse(BaseModel):
    days: int
    growth: List[GrowthData]

def setup_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    McpServerRegistry.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = datetime.now()
    servers = [
        McpServerRegistry(
            server_id="server1",
            name="Server 1",
            first_seen=now - timedelta(days=2),
            last_seen=now,
            registry_source="source1",
            risk_tier="low",
            trust_score=0.8,
            url="http://server1.com",
            verdict="clean",
            verdict_reasoning="No issues found",
        ),
        McpServerRegistry(
            server_id="server2",
            name="Server 2",
            first_seen=now - timedelta(days=1),
            last_seen=now,
            registry_source="source2",
            risk_tier="medium",
            trust_score=0.5,
            url="http://server2.com",
            verdict="clean",
            verdict_reasoning="No issues found",
        ),
        McpServerRegistry(
            server_id="server3",
            name="Server 3",
            first_seen=now,
            last_seen=now,
            registry_source="source3",
            risk_tier="high",
            trust_score=0.2,
            url="http://server3.com",
            verdict="clean",
            verdict_reasoning="No issues found",
        ),
    ]
    db.add_all(servers)
    db.commit()
    return db

def get_db():
    db = setup_test_db()
    try:
        yield db
    finally:
        db.close()

@app.get("/api/registry/growth", response_model=GrowthResponse)
async def get_registry_growth(days: int, db=Depends(get_db)):
    if days <= 0:
        raise HTTPException(status_code=400, detail="Days must be a positive integer")

    now = datetime.now()
    start_date = now - timedelta(days=days)

    query = select(McpServerRegistry.first_seen).where(
        McpServerRegistry.first_seen >= start_date
    )
    result = db.execute(query).fetchall()

    growth_data = {}
    for row in result:
        date = row[0].date()
        growth_data[date] = growth_data.get(date, 0) + 1

    growth = [
        GrowthData(date=str(date), count=count)
        for date, count in sorted(growth_data.items())
    ]

    return GrowthResponse(days=days, growth=growth)

if __name__ == "__main__":
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_db
    test_app.include_router(app.router)

    client = TestClient(test_app)

    response = client.get("/api/registry/growth?days=3")
    assert response.status_code == 200

    data = response.json()
    assert len(data["growth"]) == 3
    assert data["growth"][0]["count"] == 1

    print("PASS")