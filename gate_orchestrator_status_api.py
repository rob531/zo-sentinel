import datetime
from typing import Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# --- Pydantic Models ---
class ServiceHealth(BaseModel):
    service_name: str
    last_heartbeat: datetime.datetime
    status: str
    meta: Dict

# --- Database Setup (In-memory SQLite for testing) ---
DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- SQLAlchemy Model (for DB interaction) ---
from sqlalchemy import Column, DateTime, String, MetaData, Table

metadata = MetaData()
service_health_table = Table(
    "service_health",
    metadata,
    Column("service_name", String, primary_key=True),
    Column("last_heartbeat", DateTime, nullable=False),
    Column("status", String, nullable=False),
    Column("meta", String, nullable=True),  # Storing JSON as string for simplicity
)

metadata.create_all(engine)

# --- FastAPI App and Router ---
app = FastAPI()

@app.get("/gate_orchestrator/status")
def get_gate_orchestrator_status():
    db = SessionLocal()
    try:
        result = db.execute(
            service_health_table.select().where(
                service_health_table.c.service_name == "gate_orchestrator"
            )
        ).fetchone()

        if result:
            # Assuming meta is stored as a JSON string
            import json
            meta_dict = json.loads(result.meta) if result.meta else {}
            return {
                "status": result.status,
                "last_heartbeat": result.last_heartbeat.isoformat(),
                "meta": meta_dict,
            }
        else:
            return {"status": "stale", "last_heartbeat": None, "meta": {}}
    finally:
        db.close()

# --- Test Client Setup ---
client = TestClient(app)

# --- Test Cases ---
def seed_db(db_session):
    now = datetime.datetime.now()
    db_session.execute(
        service_health_table.insert().values(
            service_name="gate_orchestrator",
            last_heartbeat=now,
            status="ok",
            meta='{"version": "1.2.3"}',
        )
    )
    db_session.commit()

def test_gate_orchestrator_status_ok():
    db = SessionLocal()
    seed_db(db)
    db.close()

    response = client.get("/gate_orchestrator/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["last_heartbeat"] is not None
    assert data["meta"] == {"version": "1.2.3"}

def test_gate_orchestrator_status_stale():
    # No seeding, so it should be stale
    response = client.get("/gate_orchestrator/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stale"
    assert data["last_heartbeat"] is None
    assert data["meta"] == {}

# --- Main Execution Block for Self-Test ---
if __name__ == "__main__":
    # Re-create tables for the in-memory DB for the __main__ block
    metadata.drop_all(engine)
    metadata.create_all(engine)

    # Seed the database
    db = SessionLocal()
    seed_db(db)
    db.close()

    # Run tests using TestClient
    print("Running self-test for /gate_orchestrator/status...")

    # Test case 1: OK status
    response_ok = client.get("/gate_orchestrator/status")
    assert response_ok.status_code == 200
    data_ok = response_ok.json()
    assert data_ok["status"] == "ok"
    assert data_ok["last_heartbeat"] is not None
    assert data_ok["meta"] == {"version": "1.2.3"}
    print("Test Case 1 (OK Status): PASSED")

    # Test case 2: Stale status (clear the DB entry)
    db_clear = SessionLocal()
    db_clear.execute(service_health_table.delete().where(service_health_table.c.service_name == "gate_orchestrator"))
    db_clear.commit()
    db_clear.close()

    response_stale = client.get("/gate_orchestrator/status")
    assert response_stale.status_code == 200
    data_stale = response_stale.json()
    assert data_stale["status"] == "stale"
    assert data_stale["last_heartbeat"] is None
    assert data_stale["meta"] == {}
    print("Test Case 2 (Stale Status): PASSED")

    print("\nAll self-tests passed!")