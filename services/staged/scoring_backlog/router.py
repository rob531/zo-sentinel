from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter(prefix="/api/scoring")

class ServerInfo(BaseModel):
    server_id: int
    name: str
    url: str
    registry_source: str
    first_seen: str
    scan_count: int

class BacklogResponse(BaseModel):
    total: int
    servers: List[ServerInfo]

@router.get("/backlog", response_model=BacklogResponse)
async def get_scoring_backlog(session: Session = Depends(get_session)):
    subquery = session.query(
        McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.scored_at.isnot(None)
    ).subquery()

    query = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.registry_source,
        McpServerRegistry.first_seen,
        McpServerRegistry.scan_count
    ).outerjoin(
        subquery, McpServerRegistry.server_id == subquery.c.server_id
    ).filter(
        subquery.c.server_id.is_(None)
    ).all()

    servers = [
        ServerInfo(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            registry_source=server.registry_source,
            first_seen=server.first_seen.isoformat() if server.first_seen else None,
            scan_count=server.scan_count
        )
        for server in query
    ]

    return BacklogResponse(total=len(servers), servers=servers)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        session.execute(
            McpServerRegistry.__table__.insert(),
            [
                {"server_id": 1, "name": "scored", "url": "http://scored", "registry_source": "test", "first_seen": "2023-01-01", "scan_count": 1},
                {"server_id": 2, "name": "unscored1", "url": "http://unscored1", "registry_source": "test", "first_seen": "2023-01-02", "scan_count": 2},
                {"server_id": 3, "name": "unscored2", "url": "http://unscored2", "registry_source": "test", "first_seen": "2023-01-03", "scan_count": 3},
            ]
        )
        session.execute(
            McpLlmAxisScore.__table__.insert(),
            [
                {"server_id": 1, "scored_at": "2023-01-01"},
            ]
        )
        session.commit()

    client = TestClient(app)
    response = client.get("/api/scoring/backlog")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["servers"][0]["server_id"] == 2
    assert data["servers"][1]["server_id"] == 3
    print("PASS")