from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
import requests
import json
from datetime import datetime

router = APIRouter()

class AuditRow(BaseModel):
    server_id: str
    name: str
    url: str
    risk_tier: str
    verdict: str
    axis_labels: Dict[str, float]
    human_verdict: Optional[str] = None

class AuditReport(BaseModel):
    report_date: str
    model_version: str
    rows: List[AuditRow]

def get_latest_model_version(session) -> str:
    """Get the latest model version from MCPLLMAxisScores."""
    result = session.execute(
        "SELECT DISTINCT model_version FROM mcp_llm_axis_scores ORDER BY model_version DESC LIMIT 1"
    )
    return result.scalar() or "unknown"

def get_stratified_sample(session, model_version: str, sample_size: int = 5) -> List[Dict]:
    """Get a stratified sample of servers with their latest scores."""
    query = f"""
    WITH latest_scores AS (
        SELECT
            s.server_id,
            s.model_version,
            s.overall_risk,
            s.auth_strength,
            s.capability_breadth,
            s.data_sensitivity,
            s.network_egress,
            s.maintainer_trust,
            s.exploit_surface,
            ROW_NUMBER() OVER (PARTITION BY s.server_id ORDER BY s.model_version DESC) as rn
        FROM mcp_llm_axis_scores s
        WHERE s.model_version = '{model_version}'
    ),
    risk_tiers AS (
        SELECT
            r.server_id,
            r.name,
            r.url,
            CASE
                WHEN ls.overall_risk < 0.2 THEN 'low'
                WHEN ls.overall_risk < 0.5 THEN 'medium'
                ELSE 'high'
            END as risk_tier,
            ls.overall_risk,
            ls.auth_strength,
            ls.capability_breadth,
            ls.data_sensitivity,
            ls.network_egress,
            ls.maintainer_trust,
            ls.exploit_surface
        FROM mcp_server_registry r
        JOIN latest_scores ls ON r.server_id = ls.server_id
        WHERE ls.rn = 1
    )
    SELECT
        server_id,
        name,
        url,
        risk_tier,
        overall_risk as verdict,
        json_build_object(
            'overall_risk', overall_risk,
            'auth_strength', auth_strength,
            'capability_breadth', capability_breadth,
            'data_sensitivity', data_sensitivity,
            'network_egress', network_egress,
            'maintainer_trust', maintainer_trust,
            'exploit_surface', exploit_surface
        ) as axis_labels
    FROM risk_tiers
    ORDER BY risk_tier, overall_risk DESC
    LIMIT {sample_size}
    """
    result = session.execute(query)
    return [dict(row) for row in result]

@router.get("/audit_report", response_model=AuditReport)
async def generate_audit_report(
    session=Depends(get_session),
    sample_size: int = 5
) -> AuditReport:
    """Generate a scoring precision audit report with stratified sampling."""
    model_version = get_latest_model_version(session)
    if not model_version:
        raise HTTPException(status_code=404, detail="No model version found")

    rows = get_stratified_sample(session, model_version, sample_size)
    if not rows:
        raise HTTPException(status_code=404, detail="No servers found for the latest model version")

    return AuditReport(
        report_date=datetime.now().isoformat(),
        model_version=model_version,
        rows=[AuditRow(**row) for row in rows]
    )

@router.get("/audit_report/markdown", response_model=str)
async def generate_audit_report_markdown(
    session=Depends(get_session),
    sample_size: int = 5
) -> str:
    """Generate a markdown version of the audit report."""
    report = await generate_audit_report(session, sample_size)

    markdown = f"# Scoring Precision Audit Report\n\n"
    markdown += f"**Generated:** {report.report_date}\n"
    markdown += f"**Model Version:** {report.model_version}\n\n"

    markdown += "| Server ID | Name | URL | Risk Tier | Verdict | Human Verdict |\n"
    markdown += "|-----------|------|-----|-----------|---------|---------------|\n"

    for row in report.rows:
        markdown += f"| {row.server_id} | {row.name} | {row.url} | {row.risk_tier} | {row.verdict:.2f} | {row.human_verdict or ''} |\n"

    markdown += "\n## Axis Labels\n"
    for row in report.rows:
        markdown += f"\n### {row.name}\n"
        for axis, score in row.axis_labels.items():
            markdown += f"- {axis}: {score:.2f}\n"

    return markdown

@router.post("/audit_report/summarize")
async def summarize_audit_report(
    report: AuditReport
) -> Dict:
    """Summarize the audit report."""
    summary = {
        "total_servers": len(report.rows),
        "risk_tier_counts": {
            "low": 0,
            "medium": 0,
            "high": 0
        },
        "average_scores": {
            "overall_risk": 0,
            "auth_strength": 0,
            "capability_breadth": 0,
            "data_sensitivity": 0,
            "network_egress": 0,
            "maintainer_trust": 0,
            "exploit_surface": 0
        }
    }

    for row in report.rows:
        summary["risk_tier_counts"][row.risk_tier] += 1
        for axis, score in row.axis_labels.items():
            summary["average_scores"][axis] += score

    for axis in summary["average_scores"]:
        summary["average_scores"][axis] /= len(report.rows)

    return summary

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test1",
        name="Test Server 1",
        url="http://test1.example.com"
    )
    test_session.add(test_server)
    test_session.commit()

    test_score = MCPLLMAxisScores(
        server_id="test1",
        model_version="v1.0",
        overall_risk=0.3,
        auth_strength=0.4,
        capability_breadth=0.5,
        data_sensitivity=0.2,
        network_egress=0.6,
        maintainer_trust=0.7,
        exploit_surface=0.1
    )
    test_session.add(test_score)
    test_session.commit()

    # Test the API
    client = TestClient(app)

    # Test audit report
    response = client.get("/audit_report")
    assert response.status_code == 200
    report = response.json()
    assert report["model_version"] == "v1.0"
    assert len(report["rows"]) == 1
    assert report["rows"][0]["server_id"] == "test1"

    # Test markdown report
    response = client.get("/audit_report/markdown")
    assert response.status_code == 200
    assert "Test Server 1" in response.text

    # Test summarize
    response = client.post("/audit_report/summarize", json=report)
    assert response.status_code == 200
    summary = response.json()
    assert summary["total_servers"] == 1
    assert summary["risk_tier_counts"]["medium"] == 1
    assert summary["average_scores"]["overall_risk"] == 0.3

    print("PASS")