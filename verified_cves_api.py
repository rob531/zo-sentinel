from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpVerifiedCves

router = APIRouter()

class VerifiedCVE(BaseModel):
    id: int
    cve_id: str
    description: str
    severity: str
    published_date: str
    last_modified_date: str
    verified_by: str
    verification_date: str

class PaginatedVerifiedCVEs(BaseModel):
    items: List[VerifiedCVE]
    total: int
    page: int
    per_page: int

@router.get("/verified_cves", response_model=PaginatedVerifiedCVEs)
async def get_verified_cves(
    session: Session = Depends(get_session),
    page: int = 1,
    per_page: int = 10
):
    offset = (page - 1) * per_page
    total = session.query(McpVerifiedCves).count()
    verified_cves = session.query(McpVerifiedCves).offset(offset).limit(per_page).all()

    return PaginatedVerifiedCVEs(
        items=[
            VerifiedCVE(
                id=cve.id,
                cve_id=cve.cve_id,
                description=cve.description,
                severity=cve.severity,
                published_date=str(cve.published_date),
                last_modified_date=str(cve.last_modified_date),
                verified_by=cve.verified_by,
                verification_date=str(cve.verification_date)
            ) for cve in verified_cves
        ],
        total=total,
        page=page,
        per_page=per_page
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpVerifiedCves
    from app.dependency_overrides import dependency_overrides

    # Create a test database
    Base.metadata.create_all(engine)

    # Override the session for testing
    from sqlalchemy.orm import sessionmaker
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create a test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Add test data
    test_session = TestSessionLocal()
    test_session.add_all([
        McpVerifiedCves(
            cve_id="CVE-2021-1234",
            description="Test CVE 1",
            severity="High",
            published_date="2021-01-01",
            last_modified_date="2021-01-02",
            verified_by="test_user",
            verification_date="2021-01-03"
        ),
        McpVerifiedCves(
            cve_id="CVE-2021-5678",
            description="Test CVE 2",
            severity="Medium",
            published_date="2021-01-04",
            last_modified_date="2021-01-05",
            verified_by="test_user",
            verification_date="2021-01-06"
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/verified_cves")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    print("PASS")