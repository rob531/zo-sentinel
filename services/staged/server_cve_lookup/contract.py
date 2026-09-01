from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from sqlalchemy.orm import Session
from sqlalchemy import select, join

router = APIRouter(prefix="/api")

class Advisory(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    source_url: str
    match_confidence: Optional[str] = None

class ServerCVEResponse(BaseModel):
    server_id: int
    server_name: str
    advisories: List[Advisory]

def get_server_cves(server_id: int, db: Session = Depends(get_session)) -> ServerCVEResponse:
    query = (
        select(
            McpServerRegistry.id,
            McpServerRegistry.name,
            VulnAdvisory.id,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.published_at,
            VulnAdvisory.source_url,
            VulnLink.match_confidence
        )
        .select_from(
            join(
                McpServerRegistry,
                VulnLink,
                McpServerRegistry.id == VulnLink.server_id
            )
            .join(
                VulnAdvisory,
                VulnAdvisory.id == VulnLink.advisory_id
            )
        )
        .where(McpServerRegistry.id == server_id)
    )

    result = db.execute(query).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail="Server not found")

    server_id, server_name, *advisories = zip(*result)
    server_id = server_id[0]
    server_name = server_name[0]

    return ServerCVEResponse(
        server_id=server_id,
        server_name=server_name,
        advisories=[
            Advisory(
                id=id,
                summary=summary,
                severity=severity,
                ecosystem=ecosystem,
                package=package,
                published_at=published_at,
                source_url=source_url,
                match_confidence=match_confidence
            )
            for id, summary, severity, ecosystem, package, published_at, source_url, match_confidence in advisories
        ]
    )

@router.get("/servers/{server_id}/cves", response_model=ServerCVEResponse)
async def server_cve_lookup(server_id: int, db: Session = Depends(get_session)):
    return get_server_cves(server_id, db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        # Add a test server
        test_server = McpServerRegistry(id=1, name="Test Server")
        db.add(test_server)

        # Add test advisories
        test_advisories = [
            VulnAdvisory(
                id=1,
                summary="Test Advisory 1",
                severity="high",
                ecosystem="python",
                package="test-package-1",
                published_at="2023-01-01",
                source_url="https://example.com/advisory1"
            ),
            VulnAdvisory(
                id=2,
                summary="Test Advisory 2",
                severity="medium",
                ecosystem="python",
                package="test-package-2",
                published_at="2023-01-02",
                source_url="https://example.com/advisory2"
            ),
            VulnAdvisory(
                id=3,
                summary="Test Advisory 3",
                severity="low",
                ecosystem="python",
                package="test-package-3",
                published_at="2023-01-03",
                source_url="https://example.com/advisory3"
            )
        ]
        db.add_all(test_advisories)

        # Add test vuln links
        test_links = [
            VulnLink(server_id=1, advisory_id=1, match_confidence="high"),
            VulnLink(server_id=1, advisory_id=2, match_confidence="medium")
        ]
        db.add_all(test_links)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/servers/1/cves")

    assert response.status_code == 200
    data = response.json()
    assert len(data["advisories"]) >= 1
    assert "severity" in data["advisories"][0]

    print("PASS")