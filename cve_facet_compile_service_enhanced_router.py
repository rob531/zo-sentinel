from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from app.db import get_session
from app.models import McpVulnAdvisories, McpVulnLinks
from cve_facet_compile_service_enhanced import compile_facets
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import json

router = APIRouter()

class FacetResponse(BaseModel):
    facets: Dict[str, Any]

@router.get("/cve/facet/compile", response_model=FacetResponse)
async def get_compiled_facets(db: Session = Depends(get_session)):
    try:
        advisories = db.query(McpVulnAdvisories).all()
        links = db.query(McpVulnLinks).all()

        compiled_facets = compile_facets(advisories, links)

        return {"facets": compiled_facets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Override the database session for testing
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Seed test data
    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Add test data
    with TestingSessionLocal() as db:
        test_advisory = McpVulnAdvisories(
            id=1,
            cve_id="CVE-2023-1234",
            description="Test vulnerability",
            severity="High",
            published_date="2023-01-01"
        )
        db.add(test_advisory)

        test_link = McpVulnLinks(
            id=1,
            cve_id="CVE-2023-1234",
            url="https://example.com/vuln",
            source="Example Source"
        )
        db.add(test_link)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/cve/facet/compile")
    assert response.status_code == 200
    print("PASS")