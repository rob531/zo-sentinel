from typing import List, Dict, Optional
from pydantic import BaseModel
from fastapi import Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpAdvisoryLinks
import requests

class AdvisoryFinding(BaseModel):
    advisory_id: str
    source: str
    severity: str
    link_confidence: float

class FindingsPayload(BaseModel):
    server_id: str
    verdict: str
    axes: List[float]
    findings: List[AdvisoryFinding]

def get_verdict_findings(server_id: str, session=Depends(get_session)) -> Optional[FindingsPayload]:
    # Get verdict and axis scores
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        return None

    axes = session.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).all()
    axes_scores = [axis.score for axis in sorted(axes, key=lambda x: x.axis_id)]

    # Get linked advisories
    findings = session.query(McpAdvisoryLinks).filter(McpAdvisoryLinks.server_id == server_id).all()
    advisory_findings = [
        AdvisoryFinding(
            advisory_id=finding.advisory_id,
            source=finding.source,
            severity=finding.severity,
            link_confidence=finding.link_confidence
        )
        for finding in findings
    ]

    return FindingsPayload(
        server_id=server_id,
        verdict=server.verdict,
        axes=axes_scores,
        findings=advisory_findings
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the session dependency for testing
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    session = SessionLocal()
    test_server = McpServerRegistry(
        server_id="test_server_1",
        verdict="safe",
        confidence=0.9
    )
    session.add(test_server)

    # Add axis scores
    for axis_id in range(1, 8):
        session.add(McpLlmAxisScores(
            server_id="test_server_1",
            axis_id=axis_id,
            score=0.8
        ))

    # Add advisory links
    session.add(McpAdvisoryLinks(
        server_id="test_server_1",
        advisory_id="CVE-2023-1234",
        source="NVD",
        severity="high",
        link_confidence=0.95
    ))
    session.add(McpAdvisoryLinks(
        server_id="test_server_1",
        advisory_id="CVE-2023-5678",
        source="GitHub",
        severity="medium",
        link_confidence=0.85
    ))
    session.commit()

    # Test with linked advisories
    payload = get_verdict_findings("test_server_1")
    assert payload is not None
    assert payload.server_id == "test_server_1"
    assert payload.verdict == "safe"
    assert len(payload.axes) == 7
    assert len(payload.findings) == 2

    # Test with no linked advisories
    test_server_no_links = McpServerRegistry(
        server_id="test_server_2",
        verdict="safe",
        confidence=0.9
    )
    session.add(test_server_no_links)
    for axis_id in range(1, 8):
        session.add(McpLlmAxisScores(
            server_id="test_server_2",
            axis_id=axis_id,
            score=0.8
        ))
    session.commit()

    payload_no_links = get_verdict_findings("test_server_2")
    assert payload_no_links is not None
    assert payload_no_links.server_id == "test_server_2"
    assert payload_no_links.verdict == "safe"
    assert len(payload_no_links.axes) == 7
    assert len(payload_no_links.findings) == 0

    print("PASS")