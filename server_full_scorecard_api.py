from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import httpx
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
from datetime import datetime

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    probs: List[float]
    escalated: bool

class CVEExposure(BaseModel):
    count: int
    top_severity: str

class ServerScorecardResponse(BaseModel):
    server_id: str
    server_name: str
    registry_source: str
    last_scanned: datetime
    scan_count: int
    risk_tier: str
    verdict_reasoning: str
    overall_score: float
    axes: List[AxisScore]
    cve_exposure: CVEExposure
    has_open_dispute: bool
    scored_at: datetime

async def query_write_service(query: str, params: list = None):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params}
        )
        response.raise_for_status()
        return response.json()

async def get_server_metadata(session, server_id: str):
    server = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

async def get_axis_scores(server_id: str):
    query = """
    SELECT axis_name, label, p_top, p_critical, probs, escalated
    FROM mcp_llm_axis_scores
    WHERE server_id = ?
    """
    result = await query_write_service(query, [server_id])
    return result

async def get_cve_exposure(server_id: str):
    query = """
    SELECT COUNT(*) as count, MAX(severity) as top_severity
    FROM vuln_advisories
    JOIN vuln_links ON vuln_advisories.id = vuln_links.advisory_id
    WHERE vuln_links.server_id = ?
    """
    result = await query_write_service(query, [server_id])
    return result[0] if result else {"count": 0, "top_severity": None}

async def has_open_dispute(session, server_id: str):
    dispute = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server_id,
        MCPScoreDisputes.status != "resolved"
    ).first()
    return bool(dispute)

@router.get("/servers/{server_id}/scorecard", response_model=ServerScorecardResponse)
async def get_server_scorecard(server_id: str, session: Depends(get_session)):
    server = await get_server_metadata(session, server_id)
    axes = await get_axis_scores(server_id)
    cve_exposure = await get_cve_exposure(server_id)
    has_dispute = await has_open_dispute(session, server_id)

    overall_score = next(
        (axis["p_top"] for axis in axes if axis["axis_name"] == "overall_risk"),
        0.0
    )

    response = ServerScorecardResponse(
        server_id=server.server_id,
        server_name=server.server_name,
        registry_source=server.registry_source,
        last_scanned=server.last_scanned,
        scan_count=server.scan_count,
        risk_tier=server.risk_tier,
        verdict_reasoning="",
        overall_score=overall_score,
        axes=axes,
        cve_exposure=CVEExposure(
            count=cve_exposure["count"],
            top_severity=cve_exposure["top_severity"]
        ),
        has_open_dispute=has_dispute,
        scored_at=datetime.now()
    )

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from unittest.mock import patch
    import pytest

    @patch("server_full_scorecard_api.query_write_service")
    def test_scorecard(mock_query):
        mock_query.return_value = {
            "axes": [
                {"axis_name": "axis1", "label": "Label1", "p_top": 0.1, "p_critical": 0.05, "probs": [0.1, 0.2, 0.7], "escalated": False},
                {"axis_name": "axis2", "label": "Label2", "p_top": 0.2, "p_critical": 0.1, "probs": [0.2, 0.3, 0.5], "escalated": False},
                {"axis_name": "axis3", "label": "Label3", "p_top": 0.3, "p_critical": 0.15, "probs": [0.3, 0.4, 0.3], "escalated": False},
                {"axis_name": "axis4", "label": "Label4", "p_top": 0.4, "p_critical": 0.2, "probs": [0.4, 0.5, 0.1], "escalated": False},
                {"axis_name": "axis5", "label": "Label5", "p_top": 0.5, "p_critical": 0.25, "probs": [0.5, 0.6, 0.0], "escalated": False},
                {"axis_name": "axis6", "label": "Label6", "p_top": 0.6, "p_critical": 0.3, "probs": [0.6, 0.7, 0.0], "escalated": False},
                {"axis_name": "overall_risk", "label": "Overall Risk", "p_top": 0.7, "p_critical": 0.35, "probs": [0.7, 0.8, 0.0], "escalated": False}
            ],
            "cve_exposure": {"count": 2, "top_severity": "high"}
        }

        client = TestClient(app)
        response = client.get("/servers/test-server/scorecard")
        assert response.status_code == 200
        data = response.json()
        assert len(data["axes"]) == 7
        assert data["cve_exposure"]["count"] == 2
        assert 0 <= data["overall_score"] <= 1
        assert "risk_tier" in data
        print("PASS")