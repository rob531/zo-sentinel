from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import Dict
import requests
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

def get_growth_snapshot(date: str) -> Dict[str, float]:
    try:
        query_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    session = Depends(get_session)

    # Get total servers up to the given date
    total_servers_query = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.created_at <= query_date
    ).count()

    # Get new servers on the given date
    new_servers_query = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.created_at == query_date
    ).count()

    # Calculate growth percent
    if total_servers_query - new_servers_query == 0:
        growth_percent = 0.0
    else:
        growth_percent = (new_servers_query / (total_servers_query - new_servers_query)) * 100

    return {
        "new_servers": new_servers_query,
        "total_servers": total_servers_query,
        "growth_percent": growth_percent
    }

@router.get("/registry/growth_snapshot")
async def growth_snapshot(date: str):
    return get_growth_snapshot(date)

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from unittest.mock import patch
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a test app
    app = FastAPI()
    app.include_router(router)

    # Create a test client
    client = TestClient(app)

    # Create a test database
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    MCPServerRegistry.__table__.create(engine)

    # Add test data
    with SessionLocal() as session:
        session.execute(
            MCPServerRegistry.__table__.insert(),
            [
                {"id": 1, "created_at": "2023-01-01"},
                {"id": 2, "created_at": "2023-01-01"},
                {"id": 3, "created_at": "2023-01-02"},
                {"id": 4, "created_at": "2023-01-02"},
                {"id": 5, "created_at": "2023-01-02"},
            ]
        )
        session.commit()

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test the endpoint
    response = client.get("/registry/growth_snapshot?date=2023-01-01")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"new_servers", "total_servers", "growth_percent"}
    assert isinstance(data["new_servers"], int)
    assert isinstance(data["total_servers"], int)
    assert isinstance(data["growth_percent"], float)

    print("PASS")