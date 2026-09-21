from typing import List, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory, VulnLink, McpServerRegistry
from pydantic import BaseModel

class Advisory(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    source_url: str
    match_confidence: Optional[str]

class ServerCVEResponse(BaseModel):
    server_id: int
    server_name: str
    advisories: List[Advisory]

def get_server_cves(server_id: int, session: Session = Depends(get_session)) -> ServerCVEResponse:
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    advisories = session.query(
        VulnAdvisory.id,
        VulnAdvisory.summary,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.published_at,
        VulnAdvisory.source_url,
        VulnLink.match_confidence
    ).join(
        VulnLink, VulnAdvisory.id == VulnLink.advisory_id
    ).filter(
        VulnLink.server_id == server_id
    ).all()

    return ServerCVEResponse(
        server_id=server.id,
        server_name=server.name,
        advisories=[
            Advisory(
                id=advisory.id,
                summary=advisory.summary,
                severity=advisory.severity,
                ecosystem=advisory.ecosystem,
                package=advisory.package,
                published_at=str(advisory.published_at),
                source_url=advisory.source_url,
                match_confidence=advisory.match_confidence
            ) for advisory in advisories
        ]
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from datetime import datetime

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test server
        test_server = McpServerRegistry(
            id=1,
            name="Test Server",
            org_id=1,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        session.add(test_server)

        # Create test advisories
        advisory1 = VulnAdvisory(
            id=1,
            summary="Test Advisory 1",
            severity="high",
            ecosystem="npm",
            package="test-package-1",
            published_at=datetime.now(),
            source_url="https://example.com/1"
        )
        advisory2 = VulnAdvisory(
            id=2,
            summary="Test Advisory 2",
            severity="medium",
            ecosystem="pypi",
            package="test-package-2",
            published_at=datetime.now(),
            source_url="https://example.com/2"
        )
        advisory3 = VulnAdvisory(
            id=3,
            summary="Test Advisory 3",
            severity="low",
            ecosystem="rubygems",
            package="test-package-3",
            published_at=datetime.now(),
            source_url="https://example.com/3"
        )
        session.add_all([advisory1, advisory2, advisory3])

        # Create test vuln links
        vuln_link1 = VulnLink(
            server_id=1,
            advisory_id=1,
            match_confidence="high"
        )
        vuln_link2 = VulnLink(
            server_id=1,
            advisory_id=2,
            match_confidence="medium"
        )
        session.add_all([vuln_link1, vuln_link2])

        session.commit()

        # Test the function
        response = get_server_cves(1)
        assert response.server_id == 1
        assert response.server_name == "Test Server"
        assert len(response.advisories) >= 1
        assert all(hasattr(advisory, 'severity') for advisory in response.advisories)

        print("PASS")
    finally:
        session.close()