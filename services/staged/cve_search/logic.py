from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory

class CVEResult(BaseModel):
    id: int
    summary: str
    severity: str
    package: str
    published_at: str

def search_cves(db: Session, query: Optional[str] = None) -> List[CVEResult]:
    if not query:
        return []

    results = db.query(VulnAdvisory).filter(
        or_(
            VulnAdvisory.summary.ilike(f"%{query}%"),
            VulnAdvisory.severity.ilike(f"%{query}%"),
            VulnAdvisory.package.ilike(f"%{query}%")
        )
    ).all()

    return [
        CVEResult(
            id=cve.id,
            summary=cve.summary,
            severity=cve.severity,
            package=cve.package,
            published_at=str(cve.published_at)
        )
        for cve in results
    ]

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        VulnAdvisory(
            summary="Test CVE 1 summary",
            severity="high",
            package="test-package-1",
            published_at="2023-01-01"
        ),
        VulnAdvisory(
            summary="Test CVE 2 summary",
            severity="critical",
            package="test-package-2",
            published_at="2023-01-02"
        )
    ])
    test_session.commit()

    # Test search
    results = search_cves(test_session, "test")
    assert len(results) == 2
    assert results[0].summary == "Test CVE 1 summary"
    assert results[1].severity == "critical"

    print("PASS")