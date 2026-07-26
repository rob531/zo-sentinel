from typing import List
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnLink, VulnAdvisory

class CVESearchResult(BaseModel):
    id: int
    feed: str
    severity: str
    ecosystem: str
    package: str
    summary: str
    affected_ranges: str
    source_url: str
    published_at: str

def search_cves(server_id: str, session: Session = Depends(get_session)) -> List[CVESearchResult]:
    results = session.query(
        VulnLink.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.summary,
        VulnAdvisory.affected_ranges,
        VulnAdvisory.source_url,
        VulnAdvisory.published_at
    ).join(
        VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id
    ).filter(
        VulnLink.server_id == server_id
    ).all()

    return [CVESearchResult(**result._asdict()) for result in results]

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import VulnLink, VulnAdvisory
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Setup in-memory test database
    Base.metadata.create_all(bind=engine)
    app = FastAPI()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: Session(bind=engine, autocommit=True, autoflush=True)

    # Seed test data
    with Session(bind=engine) as session:
        # Seed servers
        server_1 = VulnLink(server_id="server_1", advisory_id=1)
        server_2 = VulnLink(server_id="server_2", advisory_id=2)
        session.add_all([server_1, server_2])

        # Seed advisories
        advisory_1 = VulnAdvisory(
            id=1,
            feed="nvd",
            severity="high",
            ecosystem="npm",
            package="lodash",
            summary="Critical vulnerability in lodash",
            affected_ranges="<4.17.21",
            source_url="https://nvd.nist.gov/vuln/detail/CVE-2021-1234",
            published_at="2021-01-01"
        )
        advisory_2 = VulnAdvisory(
            id=2,
            feed="nvd",
            severity="medium",
            ecosystem="npm",
            package="jquery",
            summary="Medium vulnerability in jquery",
            affected_ranges="<3.6.0",
            source_url="https://nvd.nist.gov/vuln/detail/CVE-2021-5678",
            published_at="2021-02-01"
        )
        session.add_all([advisory_1, advisory_2])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/cve/search?server_id=server_1")
    assert response.status_code == 200
    assert response.json()[0]["severity"] == "high"
    print("PASS")