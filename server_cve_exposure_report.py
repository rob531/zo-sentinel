from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class CveExposure(BaseModel):
    cve_id: str
    severity: str
    summary: str
    published_at: str
    match_confidence: float

class CveExposureResponse(BaseModel):
    server_id: str
    cves: List[CveExposure]
    total_count: int
    last_fetched: Optional[str] = None

class CveExposureSummary(BaseModel):
    total_servers: int
    total_cves: int
    servers_by_severity: dict
    cves_by_severity: dict

def get_write_service_query(server_id: str, min_confidence: float = 0.5):
    query = """
    SELECT v.id, v.summary, v.severity, v.published_at, vl.match_confidence
    FROM vuln_links vl
    JOIN vuln_advisories v ON v.id = vl.advisory_id
    WHERE vl.server_id = $1 AND vl.match_confidence >= $2
    """
    return {
        "query": query,
        "params": [server_id, min_confidence]
    }

@router.get("/servers/{server_id}/cve-exposure", response_model=CveExposureResponse)
async def get_server_cve_exposure(
    server_id: str,
    min_confidence: float = Query(0.5, ge=0, le=1),
    session: MCPServerRegistry = Depends(get_session)
):
    try:
        server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        query_data = get_write_service_query(server_id, min_confidence)
        response = requests.post("http://127.0.0.1:8772/query", json=query_data)
        response.raise_for_status()

        cves = response.json()
        return {
            "server_id": server_id,
            "cves": cves,
            "total_count": len(cves),
            "last_fetched": None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cve-exposure/summary", response_model=CveExposureSummary)
async def get_cve_exposure_summary():
    try:
        # Query for total servers
        servers_response = requests.get("http://127.0.0.1:8772/query", json={
            "query": "SELECT COUNT(DISTINCT server_id) FROM vuln_links",
            "params": []
        })
        servers_response.raise_for_status()
        total_servers = servers_response.json()[0]["count"]

        # Query for total CVEs
        cves_response = requests.get("http://127.0.0.1:8772/query", json={
            "query": "SELECT COUNT(DISTINCT advisory_id) FROM vuln_links",
            "params": []
        })
        cves_response.raise_for_status()
        total_cves = cves_response.json()[0]["count"]

        # Query for servers by severity
        servers_by_severity_response = requests.get("http://127.0.0.1:8772/query", json={
            "query": """
            SELECT v.severity, COUNT(DISTINCT vl.server_id)
            FROM vuln_links vl
            JOIN vuln_advisories v ON v.id = vl.advisory_id
            GROUP BY v.severity
            """,
            "params": []
        })
        servers_by_severity_response.raise_for_status()
        servers_by_severity = {row["severity"]: row["count"] for row in servers_by_severity_response.json()}

        # Query for CVEs by severity
        cves_by_severity_response = requests.get("http://127.0.0.1:8772/query", json={
            "query": """
            SELECT v.severity, COUNT(DISTINCT v.id)
            FROM vuln_links vl
            JOIN vuln_advisories v ON v.id = vl.advisory_id
            GROUP BY v.severity
            """,
            "params": []
        })
        cves_by_severity_response.raise_for_status()
        cves_by_severity = {row["severity"]: row["count"] for row in cves_by_severity_response.json()}

        return {
            "total_servers": total_servers,
            "total_cves": total_cves,
            "servers_by_severity": servers_by_severity,
            "cves_by_severity": cves_by_severity
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import get_session
    from unittest.mock import patch

    app = FastAPI()
    app.include_router(router)

    @patch("requests.post")
    def test_get_server_cve_exposure(mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {"id": "CVE-2023-1234", "summary": "Test CVE 1", "severity": "High", "published_at": "2023-01-01", "match_confidence": 0.9},
            {"id": "CVE-2023-5678", "summary": "Test CVE 2", "severity": "Medium", "published_at": "2023-02-01", "match_confidence": 0.8},
            {"id": "CVE-2023-9012", "summary": "Test CVE 3", "severity": "Low", "published_at": "2023-03-01", "match_confidence": 0.7}
        ]

        client = TestClient(app)
        response = client.get("/servers/srv_test/cve-exposure")
        assert response.status_code == 200
        assert len(response.json()["cves"]) == 3
        assert response.json()["server_id"] == "srv_test"
        assert response.json()["total_count"] == 3
        print("PASS")

    test_get_server_cve_exposure()