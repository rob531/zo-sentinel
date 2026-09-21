# services/staged/definition_history/contract.py
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import Base

router = APIRouter(prefix="/api", tags=["definition_history"])


class HistoryChangeEntry(BaseModel):
    date: str
    change: str


class HistoryResponse(BaseModel):
    server: str
    timeline: List[HistoryChangeEntry]


def get_definition_history(db=Depends(get_session), server: str = Query(...)) -> dict:
    result = db.execute(
        text("SELECT change_date, change_desc FROM mcp_definition_history WHERE server_id = :server ORDER BY change_date ASC"),
        {"server": server}
    )
    rows = result.fetchall()
    return {
        "server": server,
        "timeline": [{"date": row[0], "change": row[1]} for row in rows]
    }


@router.get("/history", response_model=HistoryResponse)
def history_endpoint(server: str = Query(...), db=Depends(get_session)):
    return get_definition_history(db, server)


def get_test_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def seed_db():
        with TestingSessionLocal() as session:
            session.execute(text("""
                CREATE TABLE mcp_definition_history (
                    change_date TEXT NOT NULL,
                    change_desc TEXT NOT NULL,
                    server_id TEXT NOT NULL
                )
            """))
            session.execute(text(
                "INSERT INTO mcp_definition_history (server_id, change_date, change_desc) VALUES (:server, :date, :desc)"
            ), {"server": "srv_001", "date": "2024-01-01", "desc": "Initial definition"})
            session.execute(text(
                "INSERT INTO mcp_definition_history (server_id, change_date, change_desc) VALUES (:server, :date, :desc)"
            ), {"server": "srv_001", "date": "2024-02-15", "desc": "Updated definitions"})
            session.commit()
    
    def override_get_session():
        yield TestingSessionLocal()
    
    seed_db()
    
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    return TestClient(app)


def run_test():
    client = get_test_client()
    response = client.get("/api/history?server=srv_001")
    assert response.status_code == 200
    data = response.json()
    assert data["server"] == "srv_001"
    assert len(data["timeline"]) == 2
    print("PASS")


if __name__ == "__main__":
    run_test()