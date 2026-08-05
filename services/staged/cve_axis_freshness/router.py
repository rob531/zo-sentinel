from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, VulnLink
from pydantic import BaseModel
from typing import Dict, List

router = APIRouter(prefix="/api")

class AxisFreshness(BaseModel):
    label: str
    p_top: float
    freshness_score: float

class FreshnessResponse(BaseModel):
    axes: Dict[str, AxisFreshness]

@router.get("/cve/freshness", response_model=FreshnessResponse)
def get_cve_freshness(session: Session = Depends(get_session)):
    query = session.query(
        McpLlmAxisScore.axis,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
        VulnLink.freshness_score
    ).join(
        VulnLink,
        McpLlmAxisScore.axis == VulnLink.axis
    ).all()

    axes = {}
    for axis, label, p_top, freshness_score in query:
        axes[axis] = AxisFreshness(
            label=label,
            p_top=p_top,
            freshness_score=freshness_score
        )

    return {"axes": axes}

if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed the database
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE McpLlmAxisScore (
            axis TEXT PRIMARY KEY,
            label TEXT,
            p_top REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE VulnLink (
            axis TEXT PRIMARY KEY,
            freshness_score REAL
        )
    ''')
    cursor.execute('''
        INSERT INTO McpLlmAxisScore (axis, label, p_top)
        VALUES ('axis1', 'Label1', 0.9)
    ''')
    cursor.execute('''
        INSERT INTO McpLlmAxisScore (axis, label, p_top)
        VALUES ('axis2', 'Label2', 0.8)
    ''')
    cursor.execute('''
        INSERT INTO VulnLink (axis, freshness_score)
        VALUES ('axis1', 0.7)
    ''')
    cursor.execute('''
        INSERT INTO VulnLink (axis, freshness_score)
        VALUES ('axis2', 0.6)
    ''')
    conn.commit()
    conn.close()

    # Override the dependency
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/cve/freshness")

    assert response.status_code == 200
    data = response.json()
    assert "axes" in data
    assert "axis1" in data["axes"]
    assert "axis2" in data["axes"]
    assert 0 <= data["axes"]["axis1"]["freshness_score"] <= 1
    assert 0 <= data["axes"]["axis2"]["freshness_score"] <= 1

    print("PASS")