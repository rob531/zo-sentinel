from typing import List, Optional
from fastapi import FastAPI, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select, or_
from sqlalchemy.orm import sessionmaker, Session
from app.db import get_session
from app.models import McpServerRegistry

app = FastAPI()


class ServerResult(BaseModel):
    server_id: int
    name: str
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[ServerResult]


@app.get("/api/registry/search", response_model=SearchResponse)
def search_registry(
    q: str = Query(...),
    session: Session = Depends(get_session)
) -> SearchResponse:
    stmt = select(McpServerRegistry).where(
        or_(
            McpServerRegistry.name.ilike(f"%{q}%"),
            McpServerRegistry.description.ilike(f"%{q}%")
        )
    )
    results = session.execute(stmt).scalars().all()
    return SearchResponse(results=[
        ServerResult(
            server_id=r.server_id,
            name=r.name,
            risk_tier=r.risk_tier,
            verdict=r.verdict
        ) for r in results
    ])


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    Base = McpServerRegistry.__bases__[0].__bases__[0]

    Base.metadata.create_all(engine)

    session = SessionLocal()
    session.add(McpServerRegistry(server_id=1, name="alpha_server", description="Primary alpha", risk_tier="low", verdict="safe"))
    session.add(McpServerRegistry(server_id=2, name="beta_server", description="Beta deployment", risk_tier="medium", verdict="caution"))
    session.add(McpServerRegistry(server_id=3, name="gamma_server", description="Gamma production", risk_tier="high", verdict="risky"))
    session.commit()

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    resp = client.get("/api/registry/search?q=alpha")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["server_id"] == 1
    assert data["results"][0]["name"] == "alpha_server"

    resp = client.get("/api/registry/search?q=server")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 3

    resp = client.get("/api/registry/search?q=production")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1
    assert resp.json()["results"][0]["verdict"] == "risky"

    print("PASS")