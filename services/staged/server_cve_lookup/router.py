from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, vuln_advisories, VulnLink

from .contract import Advisory, ServerCVEResponse
from .logic import get_server_cves

router = APIRouter(prefix="/api")

@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCVEResponse,
    responses={404: {"description": "Server not found"}},
)
async def get_cves_for_server(
    server_id: int,
    session: Session = Depends(get_session),
) -> ServerCVEResponse:
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    advisories = get_server_cves(session, server_id)

    return ServerCVEResponse(
        server_id=server.id,
        server_name=server.name,
        advisories=advisories
    )

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

    # Test app
    app = FastAPI()
    app.include_router(router)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    def seed_test_data():
        session = SessionLocal()
        try:
            # Add a test server
            test_server = McpServerRegistry(
                id=1,
                name="Test Server",
                org_id=1,
                created_at="2023-01-01",
                updated_at="2023-01-01"
            )
            session.add(test_server)

            # Add test advisories
            advisories = [
                vuln_advisories(
                    id=1,
                    summary="Test Advisory 1",
                    severity="High",
                    ecosystem="Python",
                    package="test-package-1",
                    published_at="2023-01-01",
                    source_url="http://example.com/1"
                ),
                vuln_advisories(
                    id=2,
                    summary="Test Advisory 2",
                    severity="Medium",
                    ecosystem="Python",
                    package="test-package-2",
                    published_at="2023-01-02",
                    source_url="http://example.com/2"
                ),
                vuln_advisories(
                    id=3,
                    summary="Test Advisory 3",
                    severity="Low",
                    ecosystem="Python",
                    package="test-package-3",
                    published_at="2023-01-03",
                    source_url="http://example.com/3"
                )
            ]
            session.add_all(advisories)

            # Add test VulnLink
            vuln_links_data = [
                VulnLink(
                    server_id=1,
                    advisory_id=1,
                    match_confidence=0.9
                ),
                VulnLink(
                    server_id=1,
                    advisory_id=2,
                    match_confidence=0.8
                )
            ]
            session.add_all(vuln_links_data)

            session.commit()
        finally:
            session.close()

    seed_test_data()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/servers/1/cves")
    assert response.status_code == 200
    data = response.json()
    assert len(data["advisories"]) >= 1
    assert "severity" in data["advisories"][0]

    print("PASS")