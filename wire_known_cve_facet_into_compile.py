"""Wire known CVE facet into the compile pipeline."""
# deps: fastapi, requests, uvicorn

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, VulnAdvisory, VulnLink
from sqlalchemy.orm import Session

app = FastAPI()


class CVEDetail(BaseModel):
    id: str
    severity: Optional[str]
    summary: Optional[str]
    match_confidence: float


class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]


class KnownCVEFacetResponse(BaseModel):
    server_id: str
    cve_count: int
    cve_details: List[CVEDetail]
    axis_scores: Optional[List[AxisScore]] = None


def get_known_cve_facet(server_id: str, session: Session) -> dict:
    """Retrieve known CVE facet data for a given MCP server."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name.in_(["maintainer_trust", "exploit_surface"])
    ).all()
    
    axis_score_list = [
        AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top
        )
        for score in axis_scores
    ]
    
    vuln_links = session.query(VulnLink).filter(
        VulnLink.server_id == server_id
    ).all()
    
    cve_details = []
    advisory_ids = [link.advisory_id for link in vuln_links]
    
    if advisory_ids:
        advisories = session.query(VulnAdvisory).filter(
            VulnAdvisory.id.in_(advisory_ids)
        ).all()
        
        link_map = {link.advisory_id: link for link in vuln_links}
        
        for advisory in advisories:
            link = link_map.get(advisory.id)
            cve_details.append(CVEDetail(
                id=advisory.id,
                severity=advisory.severity,
                summary=advisory.summary,
                match_confidence=link.match_confidence if link else 0.0
            ))
    
    return {
        "server_id": server_id,
        "cve_count": len(cve_details),
        "cve_details": [cve.model_dump() for cve in cve_details],
        "axis_scores": [a.model_dump() for a in axis_score_list] if axis_score_list else None
    }


@app.get("/compile/{server_id}/cve-facet", response_model=KnownCVEFacetResponse)
async def get_cve_facet_endpoint(server_id: str, session: Session = Depends(get_session)) -> dict:
    """GET endpoint returning aggregated CVE data for a given MCP server."""
    return get_known_cve_facet(server_id, session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    import tempfile
    import os

    # Create temp SQLite DB for test isolation
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=test_engine)
        
        test_session = TestSessionLocal()
        
        # Create test data
        test_server = McpServerRegistry(server_id="test-server-001", name="Test Server")
        test_session.add(test_server)
        
        test_axis = McpLlmAxisScore(
            id=1,
            server_id="test-server-001",
            axis_name="maintainer_trust",
            label="MEDIUM",
            p_top=0.6,
            model_version="v1",
            probs={},
            escalated=False
        )
        test_session.add(test_axis)
        
        test_advisory = VulnAdvisory(
            id="CVE-2024-1234",
            feed="nvd",
            summary="Test vulnerability",
            severity="HIGH",
            source_url="https://example.com/cve"
        )
        test_session.add(test_advisory)
        
        test_link = VulnLink(
            advisory_id="CVE-2024-1234",
            server_id="test-server-001",
            match_basis="package_exact",
            match_value="test-package",
            match_confidence=1.0
        )
        test_session.add(test_link)
        
        test_session.commit()
        test_session.close()
        
        # Override dependency
        def override_get_session():
            session = TestSessionLocal()
            try:
                yield session
            finally:
                session.close()
        
        app.dependency_overrides[get_session] = override_get_session
        
        client = TestClient(app)
        response = client.get("/compile/test-server-001/cve-facet")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "cve_count" in data, "Response missing cve_count"
        assert data["cve_count"] >= 0, f"cve_count should be >= 0, got {data['cve_count']}"
        assert "server_id" in data, "Response missing server_id"
        assert data["server_id"] == "test-server-001", f"Wrong server_id: {data['server_id']}"
        assert "cve_details" in data, "Response missing cve_details"
        
        print("PASS")
    finally:
        app.dependency_overrides.clear()
        if os.path.exists(db_path):
            os.unlink(db_path)