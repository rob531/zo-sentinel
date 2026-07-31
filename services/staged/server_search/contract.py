from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import requests

app = FastAPI()

class Server(BaseModel):
    server_id: str
    name: str
    registry_source: str
    risk_tier: str
    verdict: str
    last_seen: str

class SearchResponse(BaseModel):
    servers: List[Server]

def get_servers(
    db: Session = Depends(get_session),
    q: Optional[str] = None,
    source: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 10
):
    query = db.query(McpServerRegistry)

    if q:
        query = query.filter(
            or_(
                McpServerRegistry.name.ilike(f"%{q}%"),
                McpServerRegistry.description.ilike(f"%{q}%")
            )
        )

    if source:
        query = query.filter(McpServerRegistry.registry_source == source)

    if tier:
        query = query.filter(McpServerRegistry.risk_tier == tier)

    query = query.order_by(McpServerRegistry.last_seen.desc())
    query = query.limit(limit)

    servers = query.all()

    return [
        Server(
            server_id=str(server.id),
            name=server.name,
            registry_source=server.registry_source,
            risk_tier=server.risk_tier,
            verdict=server.verdict,
            last_seen=str(server.last_seen)
        ) for server in servers
    ]

@app.get("/api/servers/search", response_model=SearchResponse)
async def search_servers(
    q: Optional[str] = None,
    source: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_session)
):
    servers = get_servers(db, q, source, tier, limit)
    return {"servers": servers}

def test_search_servers():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        test_servers = [
            McpServerRegistry(
                name="Server Alpha",
                description="Alpha server description",
                registry_source="source1",
                risk_tier="high",
                verdict="clean",
                last_seen="2023-01-01"
            ),
            McpServerRegistry(
                name="Server Beta",
                description="Beta server description",
                registry_source="source2",
                risk_tier="medium",
                verdict="clean",
                last_seen="2023-01-02"
            ),
            McpServerRegistry(
                name="Server Gamma",
                description="Gamma server description",
                registry_source="source1",
                risk_tier="low",
                verdict="clean",
                last_seen="2023-01-03"
            ),
            McpServerRegistry(
                name="Server Delta",
                description="Delta server description",
                registry_source="source3",
                risk_tier="high",
                verdict="clean",
                last_seen="2023-01-04"
            ),
            McpServerRegistry(
                name="Server Epsilon",
                description="Epsilon server description",
                registry_source="source2",
                risk_tier="medium",
                verdict="clean",
                last_seen="2023-01-05"
            )
        ]
        db.add_all(test_servers)
        db.commit()

    # Test cases
    try:
        # Test search by name
        response = requests.get(
            "http://127.0.0.1:8000/api/servers/search",
            params={"q": "Alpha"}
        )
        assert response.status_code == 200
        assert len(response.json()["servers"]) == 1
        assert response.json()["servers"][0]["name"] == "Server Alpha"

        # Test filter by tier
        response = requests.get(
            "http://127.0.0.1:8000/api/servers/search",
            params={"tier": "high"}
        )
        assert response.status_code == 200
        assert len(response.json()["servers"]) == 2
        assert all(server["risk_tier"] == "high" for server in response.json()["servers"])

        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
    finally:
        # Clean up
        app.dependency_overrides.clear()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    test_search_servers()