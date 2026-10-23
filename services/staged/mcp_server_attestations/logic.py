"""
MCP Server Attestations Service

FastAPI service for retrieving attestations for a given MCP server.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/servers", tags=["attestations"])


# Response models
class AttestationResponse(BaseModel):
    id: int
    verdict: str
    attested_at: datetime
    attested_by: str


class AttestationsListResponse(BaseModel):
    server_id: int
    attestations: list[AttestationResponse]


@router.get("/{server_id}/attestations", response_model=AttestationsListResponse)
def get_server_attestations(
    server_id: int,
    session: Session = Depends(get_session)
) -> AttestationsListResponse:
    """
    Retrieve all attestations for a specific MCP server.
    
    Args:
        server_id: The ID of the MCP server to get attestations for
        
    Returns:
        AttestationsListResponse containing server_id and list of attestations
    """
    query = select(
        text("id"),
        text("verdict"),
        text("attested_at"),
        text("attested_by")
    ).select_from(text("mcp_attestations")).where(text("server_id = :server_id")).params(server_id=server_id)
    
    result = session.execute(query)
    rows = result.fetchall()
    
    attestations = [
        AttestationResponse(
            id=row[0],
            verdict=row[1],
            attested_at=row[2],
            attested_by=row[3]
        )
        for row in rows
    ]
    
    return AttestationsListResponse(
        server_id=server_id,
        attestations=attestations
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    that_app = FastAPI()
    that_app.include_router(router)
    
    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with in_memory_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_attestations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                verdict VARCHAR(50) NOT NULL,
                attested_at TIMESTAMP NOT NULL,
                attested_by VARCHAR(255) NOT NULL
            )
        """))
        conn.commit()
        
        seed_data = [
            {"server_id": 1, "verdict": "APPROVED", "attested_at": "2024-01-15 10:00:00", "attested_by": "admin"},
            {"server_id": 1, "verdict": "REVIEWED", "attested_at": "2024-01-16 11:00:00", "attested_by": "security"},
            {"server_id": 2, "verdict": "APPROVED", "attested_at": "2024-01-17 09:00:00", "attested_by": "admin"},
        ]
        
        for seed in seed_data:
            conn.execute(text("""
                INSERT INTO mcp_attestations (server_id, verdict, attested_at, attested_by)
                VALUES (:server_id, :verdict, :attested_at, :attested_by)
            """), seed)
        conn.commit()
    
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    that_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(that_app)
    
    response = client.get("/servers/1/attestations")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == 1
    assert len(data["attestations"]) == 2
    assert data["attestations"][0]["verdict"] == "APPROVED"
    assert data["attestations"][0]["attested_by"] == "admin"
    assert data["attestations"][1]["verdict"] == "REVIEWED"
    
    print("PASS")