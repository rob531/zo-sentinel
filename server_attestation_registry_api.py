from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpAttestation
from sqlalchemy.orm import Session
from datetime import datetime
import httpx

router = APIRouter()

class AttestationResponse(BaseModel):
    server_id: str
    verdict: str
    reasoning: str
    conditions: str
    expiry: datetime
    approver: str
    approved_by: str
    created_at: datetime
    expires_at: datetime

class AttestationListResponse(BaseModel):
    attestations: List[AttestationResponse]
    total: int

@router.get("/attestations", response_model=AttestationListResponse)
async def get_attestations(
    server_id: Optional[str] = None,
    verdict: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    session: Session = Depends(get_session)
):
    query = session.query(McpAttestation)

    if server_id:
        query = query.filter(McpAttestation.server_id == server_id)
    if verdict:
        query = query.filter(McpAttestation.verdict == verdict)

    total = query.count()
    attestations = query.limit(limit).offset(offset).all()

    return {
        "attestations": [
            AttestationResponse(
                server_id=a.server_id,
                verdict=a.verdict,
                reasoning=a.reasoning,
                conditions=a.conditions,
                expiry=a.expiry,
                approver=a.approver,
                approved_by=a.approved_by,
                created_at=a.created_at,
                expires_at=a.expires_at
            ) for a in attestations
        ],
        "total": total
    }

@router.get("/attestations/{server_id}", response_model=AttestationResponse)
async def get_attestation_by_server_id(
    server_id: str,
    session: Session = Depends(get_session)
):
    attestation = session.query(McpAttestation).filter(
        McpAttestation.server_id == server_id
    ).order_by(McpAttestation.created_at.desc()).first()

    if not attestation:
        raise HTTPException(status_code=404, detail="Attestation not found")

    return AttestationResponse(
        server_id=attestation.server_id,
        verdict=attestation.verdict,
        reasoning=attestation.reasoning,
        conditions=attestation.conditions,
        expiry=attestation.expiry,
        approver=attestation.approver,
        approved_by=attestation.approved_by,
        created_at=attestation.created_at,
        expires_at=attestation.expires_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        test_attestation = McpAttestation(
            server_id="test-server-1",
            verdict="approved",
            reasoning="Test reasoning",
            conditions="Test conditions",
            expiry=datetime.now(),
            approver="test-approver",
            approved_by="test-approved-by",
            created_at=datetime.now(),
            expires_at=datetime.now()
        )
        session.add(test_attestation)
        session.commit()

    client = TestClient(app)

    # Test GET /attestations
    response = client.get("/attestations")
    assert response.status_code == 200
    assert len(response.json()["attestations"]) == 1

    # Test GET /attestations/{server_id}
    response = client.get("/attestations/test-server-1")
    assert response.status_code == 200
    assert response.json()["server_id"] == "test-server-1"

    print("PASS")