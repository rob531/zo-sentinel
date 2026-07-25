from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpVulnLink
from sqlalchemy.orm import Session

router = APIRouter()

class CveFacet(BaseModel):
    cve_id: str
    facets: Dict[str, List[str]]

class FacetCompileResponse(BaseModel):
    cves: List[CveFacet]

@router.get("/cve/facet_compile", response_model=FacetCompileResponse)
def get_cve_facets(db: Session = Depends(get_session)):
    # Query CVE facets from mcp_vuln_links
    results = db.query(
        McpVulnLink.cve_id,
        McpVulnLink.facet_type,
        McpVulnLink.facet_value
    ).all()

    # Compile facets into structured format
    cve_facets = {}
    for row in results:
        cve_id = row.cve_id
        facet_type = row.facet_type
        facet_value = row.facet_value

        if cve_id not in cve_facets:
            cve_facets[cve_id] = {}

        if facet_type not in cve_facets[cve_id]:
            cve_facets[cve_id][facet_type] = []

        cve_facets[cve_id][facet_type].append(facet_value)

    # Convert to response model format
    response = []
    for cve_id, facets in cve_facets.items():
        response.append(CveFacet(cve_id=cve_id, facets=facets))

    return FacetCompileResponse(cves=response)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpVulnLink
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_data = [
        McpVulnLink(cve_id="CVE-2023-1234", facet_type="product", facet_value="ProductA"),
        McpVulnLink(cve_id="CVE-2023-1234", facet_type="product", facet_value="ProductB"),
        McpVulnLink(cve_id="CVE-2023-1234", facet_type="vendor", facet_value="VendorX"),
        McpVulnLink(cve_id="CVE-2023-5678", facet_type="product", facet_value="ProductC"),
        McpVulnLink(cve_id="CVE-2023-5678", facet_type="vendor", facet_value="VendorY"),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/cve/facet_compile")
    assert response.status_code == 200
    assert len(response.json()["cves"]) == 2
    print("PASS")