from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()

class ServerListResponse(BaseModel):
    servers: List[dict]
    next_cursor: Optional[str]
    total: int

class ServerItem(BaseModel):
    server_id: str
    name: str
    registry_source: str
    verdict: str
    risk_tier: str
    last_assessed: str

def get_servers(
    db: Session = Depends(get_session),
    cursor: Optional[str] = None,
    limit: int = 20
) -> ServerListResponse:
    if limit > 100:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 100")

    query = select(MCPServerRegistry)

    if cursor:
        query = query.where(MCPServerRegistry.id > cursor)

    query = query.order_by(MCPServerRegistry.id).limit(limit)

    result = db.execute(query)
    servers = result.scalars().all()

    server_list = []
    for server in servers:
        server_list.append({
            "server_id": server.id,
            "name": server.name,
            "registry_source": server.registry_source,
            "verdict": server.verdict,
            "risk_tier": server.risk_tier,
            "last_assessed": server.last_assessed.isoformat() if server.last_assessed else None
        })

    next_cursor = None
    if len(server_list) == limit:
        next_cursor = server_list[-1]["server_id"]

    total = db.execute(select([func.count()]).select_from(MCPServerRegistry)).scalar()

    return ServerListResponse(
        servers=server_list,
        next_cursor=next_cursor,
        total=total
    )

@router.get("/servers", response_model=ServerListResponse)
async def list_servers(
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, le=100)
):
    return get_servers(cursor=cursor, limit=limit)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/servers?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "servers" in data
    assert "next_cursor" in data
    assert len(data["servers"]) <= 5

    print("PASS")