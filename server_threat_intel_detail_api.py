from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLM_axis_scores

router = APIRouter(prefix="/servers", tags=["threat-intel"])


class ThreatIntelIndicator(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    confidence: float


class VulnerabilityAdvisory(BaseModel):
    advisory_id: str
    severity: str
    confidence: float


class ThreatIntelDetailResponse(BaseModel):
    server_id: str
    overall_risk: float
    threat_intel: list[ThreatIntelIndicator]
    vulnerabilities: list[VulnerabilityAdvisory]


def _query_write_service(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import urllib.request
    payload = json.dumps({"query": query, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8772/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@router.get("/{server_id}/threat-intel-detail", response_model=ThreatIntelDetailResponse)
def get_threat_intel_detail(
    server_id: str,
    session: Session = Depends(get_session),
) -> ThreatIntelDetailResponse:
    score_row = (
        session.query(MCPLLM_axis_scores)
        .filter(
            MCPLLM_axis_scores.server_id == server_id,
            MCPLLM_axis_scores.axis_name == "overall_risk",
        )
        .first()
    )
    overall_risk = float(score_row.score_value) if score_row else 0.0

    threat_intel_query = """
        SELECT indicator_type, indicator_value, source_url
        FROM threat_intel_refs
        WHERE server_id = :server_id
    """
    threat_intel_rows = _query_write_service(threat_intel_query, {"server_id": server_id})
    threat_intel_list = [
        ThreatIntelIndicator(
            indicator_type=row["indicator_type"],
            indicator_value=row["indicator_value"],
            source=row["source_url"],
            confidence=1.0,
        )
        for row in threat_intel_rows
    ]

    vuln_query = """
        SELECT advisory_id, match_confidence
        FROM vuln_links
        WHERE server_id = :server_id
    """
    vuln_rows = _query_write_service(vuln_query, {"server_id": server_id})
    vuln_list = [
        VulnerabilityAdvisory(
            advisory_id=row["advisory_id"],
            severity="UNKNOWN",
            confidence=float(row["match_confidence"]),
        )
        for row in vuln_rows
    ]

    return ThreatIntelDetailResponse(
        server_id=server_id,
        overall_risk=overall_risk,
        threat_intel=threat_intel_list,
        vulnerabilities=vuln_list,
    )


if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from app.main import app
    import sqlmodel
    from app.db import get_session

    mock_threat_intel = [
        {
            "indicator_type": "malware-hash",
            "indicator_value": "abc123def456",
            "source_url": "https://threatfeed.example.com/hash/abc123",
        },
        {
            "indicator_type": "suspicious-domain",
            "indicator_value": "evil.example.com",
            "source_url": "https://threatfeed.example.com/domain/evil",
        },
    ]

    mock_vuln_links = [
        {"advisory_id": "CVE-2023-12345", "match_confidence": 0.85},
        {"advisory_id": "CVE-2024-67890", "match_confidence": 0.92},
    ]

    def mock_query_write_service(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "threat_intel_refs" in query:
            return mock_threat_intel
        elif "vuln_links" in query:
            return mock_vuln_links
        return []

    class DummyModel(sqlmodel.Model):
        server_id: str = sqlmodel.Field(primary_key=True)
        axis_name: str = sqlmodel.Field(primary_key=True)
        score_value: float = 75.5

    dummy_instance = DummyModel(server_id="srv-123", axis_name="overall_risk", score_value=75.5)

    mock_session = MagicMock(spec=sqlmodel.Session)
    mock_session.query.return_value.filter.return_value.first.return_value = dummy_instance

    app.dependency_overrides[get_session] = lambda: mock_session

    with patch("server_threat_intel_detail_api._query_write_service", mock_query_write_service):
        client = TestClient(app)
        response = client.get("/servers/srv-123/threat-intel-detail")

    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "srv-123"
    assert data["overall_risk"] == 75.5
    assert len(data["threat_intel"]) == 2
    assert data["threat_intel"][0]["indicator_type"] == "malware-hash"
    assert data["threat_intel"][0]["indicator_value"] == "abc123def456"
    assert data["threat_intel"][1]["indicator_type"] == "suspicious-domain"
    assert len(data["vulnerabilities"]) == 2
    assert data["vulnerabilities"][0]["advisory_id"] == "CVE-2023-12345"
    assert data["vulnerabilities"][1]["advisory_id"] == "CVE-2024-67890"

    print("PASS")