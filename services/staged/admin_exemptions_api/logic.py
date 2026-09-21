from datetime import datetime
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPExemption, McpServerRegistry

class Exemption(BaseModel):
    id: int
    server_id: int
    server_name: str
    granted_by: str
    reason: str
    expires_at: datetime
    created_at: datetime
    scope: str
    is_active: bool

class ExemptionResponse(BaseModel):
    exemptions: List[Exemption]

def get_exemptions(
    session: Session = Depends(get_session),
    server_id: Optional[int] = None,
    include_expired: bool = False
) -> ExemptionResponse:
    query = session.query(
        MCPExemption.id,
        MCPExemption.server_id,
        McpServerRegistry.name.label('server_name'),
        MCPExemption.granted_by,
        MCPExemption.reason,
        MCPExemption.expires_at,
        MCPExemption.created_at,
        MCPExemption.scope,
        (MCPExemption.expires_at > func.now()).label('is_active')
    ).join(
        McpServerRegistry, MCPExemption.server_id == McpServerRegistry.id
    )

    if server_id is not None:
        query = query.filter(MCPExemption.server_id == server_id)

    if not include_expired:
        query = query.filter(MCPExemption.expires_at > func.now())

    results = query.all()

    exemptions = [
        Exemption(
            id=row.id,
            server_id=row.server_id,
            server_name=row.server_name,
            granted_by=row.granted_by,
            reason=row.reason,
            expires_at=row.expires_at,
            created_at=row.created_at,
            scope=row.scope,
            is_active=row.is_active
        ) for row in results
    ]

    return ExemptionResponse(exemptions=exemptions)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    session.execute(
        McpServerRegistry.__table__.insert(),
        [
            {"id": 1, "name": "Server 1"},
            {"id": 2, "name": "Server 2"},
            {"id": 3, "name": "Server 3"},
        ]
    )
    session.execute(
        MCPExemption.__table__.insert(),
        [
            {"id": 1, "server_id": 1, "granted_by": "admin", "reason": "Test 1",
             "expires_at": datetime(2025, 1, 1), "created_at": datetime(2023, 1, 1),
             "scope": "full"},
            {"id": 2, "server_id": 2, "granted_by": "admin", "reason": "Test 2",
             "expires_at": datetime(2023, 1, 1), "created_at": datetime(2023, 1, 1),
             "scope": "partial"},
            {"id": 3, "server_id": 3, "granted_by": "admin", "reason": "Test 3",
             "expires_at": datetime(2025, 1, 1), "created_at": datetime(2023, 1, 1),
             "scope": "full"},
        ]
    )
    session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/admin/exemptions")
    assert response.status_code == 200
    data = response.json()
    assert len(data["exemptions"]) == 2  # Only 2 active exemptions

    response = client.get("/api/admin/exemptions?include_expired=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data["exemptions"]) == 3  # All 3 exemptions

    response = client.get("/api/admin/exemptions?server_id=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["exemptions"]) == 1  # Only 1 active exemption for server 1

    print("PASS")