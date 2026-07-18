from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import and_

router = APIRouter()

def get_orphan_values(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    orphaned_servers = db.query(MCPServerRegistry).join(
        MCPLLMAxisScores,
        MCPServerRegistry.server_id == MCPLLMAxisScores.server_id,
        isouter=True
    ).filter(
        and_(
            MCPServerRegistry.risk_tier == 'high',
            MCPLLMAxisScores.server_id == None
        )
    ).all()

    result = []
    for server in orphaned_servers:
        result.append({
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "meta": server.meta
        })

    return result

@router.get("/orphan-values", response_model=List[Dict[str, Any]])
async def read_orphan_values(db: Session = Depends(get_session)):
    return get_orphan_values(db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_session = TestSession()
    test_server1 = MCPServerRegistry(
        server_id="server1",
        name="Test Server 1",
        risk_tier="high",
        meta={"key": "value"}
    )
    test_server2 = MCPServerRegistry(
        server_id="server2",
        name="Test Server 2",
        risk_tier="high",
        meta={"key": "value"}
    )
    test_session.add_all([test_server1, test_server2])
    test_session.commit()

    response = client.get("/orphan-values")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["server_id"] == "server1"
    assert data[1]["server_id"] == "server2"

    print("PASS")