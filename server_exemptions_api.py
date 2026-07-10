from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPExemption, MCPServerRegistry

router = APIRouter()

class ExemptionRequest(BaseModel):
    exemption_type: str
    reason: str
    granted_by: str
    expiry_date: datetime
    scope: str

class ExemptionResponse(BaseModel):
    id: int
    server_id: str
    mcp_name: str
    exemption_type: str
    reason: str
    granted_by: str
    granted_at: datetime
    expiry_date: datetime
    scope: str
    active: bool

def get_exemption_by_id(session: Session, exemption_id: int) -> Optional[MCPExemption]:
    return session.query(MCPExemption).filter(MCPExemption.id == exemption_id).first()

def get_exemptions_by_server(session: Session, server_id: str) -> List[MCPExemption]:
    return session.query(MCPExemption).filter(MCPExemption.server_id == server_id).all()

def get_server_name(session: Session, server_id: str) -> Optional[str]:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    return server.mcp_name if server else None

@router.get("/servers/{server_id}/exemption", response_model=Optional[ExemptionResponse])
async def get_server_exemption(server_id: str, session: Session = Depends(get_session)) -> Optional[ExemptionResponse]:
    exemptions = get_exemptions_by_server(session, server_id)
    if not exemptions:
        return None

    latest_exemption = max(exemptions, key=lambda x: x.granted_at)
    mcp_name = get_server_name(session, server_id)
    if not mcp_name:
        raise HTTPException(status_code=404, detail="Server not found")

    return ExemptionResponse(
        id=latest_exemption.id,
        server_id=server_id,
        mcp_name=mcp_name,
        exemption_type=latest_exemption.exemption_type,
        reason=latest_exemption.reason,
        granted_by=latest_exemption.granted_by,
        granted_at=latest_exemption.granted_at,
        expiry_date=latest_exemption.expiry_date,
        scope=latest_exemption.scope,
        active=latest_exemption.expiry_date > datetime.now()
    )

@router.post("/servers/{server_id}/exemption", response_model=ExemptionResponse, status_code=status.HTTP_201_CREATED)
async def create_server_exemption(
    server_id: str,
    exemption: ExemptionRequest,
    session: Session = Depends(get_session)
) -> ExemptionResponse:
    mcp_name = get_server_name(session, server_id)
    if not mcp_name:
        raise HTTPException(status_code=404, detail="Server not found")

    new_exemption = MCPExemption(
        server_id=server_id,
        exemption_type=exemption.exemption_type,
        reason=exemption.reason,
        granted_by=exemption.granted_by,
        granted_at=datetime.now(),
        expiry_date=exemption.expiry_date,
        scope=exemption.scope
    )

    session.add(new_exemption)
    session.commit()
    session.refresh(new_exemption)

    return ExemptionResponse(
        id=new_exemption.id,
        server_id=server_id,
        mcp_name=mcp_name,
        exemption_type=new_exemption.exemption_type,
        reason=new_exemption.reason,
        granted_by=new_exemption.granted_by,
        granted_at=new_exemption.granted_at,
        expiry_date=new_exemption.expiry_date,
        scope=new_exemption.scope,
        active=new_exemption.expiry_date > datetime.now()
    )

@router.get("/servers/{server_id}/exemptions", response_model=List[ExemptionResponse])
async def get_server_exemptions(server_id: str, session: Session = Depends(get_session)) -> List[ExemptionResponse]:
    exemptions = get_exemptions_by_server(session, server_id)
    mcp_name = get_server_name(session, server_id)
    if not mcp_name:
        raise HTTPException(status_code=404, detail="Server not found")

    return [
        ExemptionResponse(
            id=e.id,
            server_id=server_id,
            mcp_name=mcp_name,
            exemption_type=e.exemption_type,
            reason=e.reason,
            granted_by=e.granted_by,
            granted_at=e.granted_at,
            expiry_date=e.expiry_date,
            scope=e.scope,
            active=e.expiry_date > datetime.now()
        )
        for e in exemptions
    ]

@router.delete("/servers/{server_id}/exemption/{exemption_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server_exemption(
    server_id: str,
    exemption_id: int,
    session: Session = Depends(get_session)
) -> None:
    exemption = get_exemption_by_id(session, exemption_id)
    if not exemption:
        raise HTTPException(status_code=404, detail="Exemption not found")

    if exemption.server_id != server_id:
        raise HTTPException(status_code=404, detail="Exemption not found for this server")

    session.delete(exemption)
    session.commit()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Override the database dependency for testing
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test data
    Base.metadata.create_all(bind=engine)
    session = TestSessionLocal()
    test_server = MCPServerRegistry(server_id="test-server-002", mcp_name="Test Server 002")
    session.add(test_server)
    session.commit()

    client = TestClient(app)

    # Test POST /servers/test-server-002/exemption
    response = client.post(
        "/servers/test-server-002/exemption",
        json={
            "exemption_type": "test_type",
            "reason": "test reason",
            "granted_by": "test_user",
            "expiry_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "scope": "test_scope"
        }
    )
    assert response.status_code == 201
    exemption_data = response.json()
    assert exemption_data["exemption_type"] == "test_type"
    assert exemption_data["granted_by"] == "test_user"
    assert exemption_data["active"] is True

    # Test GET /servers/test-server-002/exemption
    response = client.get("/servers/test-server-002/exemption")
    assert response.status_code == 200
    exemption_data = response.json()
    assert exemption_data["exemption_type"] == "test_type"
    assert exemption_data["granted_by"] == "test_user"
    assert exemption_data["active"] is True

    print("PASS")