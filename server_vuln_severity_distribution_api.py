from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, List, Optional
from app.db import get_session
from app.models import Server, VulnAdvisory, VulnLink
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
import requests
import json
from unittest.mock import patch

router = APIRouter()

class SeverityDistribution(BaseModel):
    severities: Dict[str, Dict[str, object]]
    total: int

class ServerSeveritySummary(BaseModel):
    server_id: str
    severity_totals: Dict[str, int]

class GlobalSeveritySummary(BaseModel):
    total_servers: int
    severity_totals: Dict[str, int]
    by_server: List[ServerSeveritySummary]

def get_vuln_severity_distribution(server_id: str, session: Session, severity_filter: Optional[List[str]] = None, limit: int = 10):
    query = session.query(VulnLink.advisory_id).filter(VulnLink.server_id == server_id)
    advisory_ids = [row[0] for row in query.all()]

    if not advisory_ids:
        return {"severities": {}, "total": 0}

    severity_query = session.query(
        VulnAdvisory.severity,
        VulnAdvisory.id
    ).filter(
        VulnAdvisory.id.in_(advisory_ids)
    )

    if severity_filter:
        severity_query = severity_query.filter(VulnAdvisory.severity.in_(severity_filter))

    severities = {}
    for severity, advisory_id in severity_query.all():
        if severity not in severities:
            severities[severity] = {"count": 0, "cves": []}
        severities[severity]["count"] += 1
        severities[severity]["cves"].append(str(advisory_id))

    for severity in severities:
        severities[severity]["cves"] = severities[severity]["cves"][:limit]

    total = sum(severity["count"] for severity in severities.values())
    return {"severities": severities, "total": total}

@router.get("/servers/{server_id}/vuln_severity_distribution", response_model=SeverityDistribution)
async def get_server_vuln_severity_distribution(
    server_id: str,
    session: Session = Depends(get_session),
    severity: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50)
):
    severity_filter = severity.split(",") if severity else None
    result = get_vuln_severity_distribution(server_id, session, severity_filter, limit)
    return result

@router.get("/servers/vuln_severity_distribution/summary", response_model=GlobalSeveritySummary)
async def get_global_vuln_severity_summary(
    session: Session = Depends(get_session),
    severity: Optional[str] = Query(None)
):
    servers = session.query(Server.id).all()
    server_ids = [row[0] for row in servers]

    severity_filter = severity.split(",") if severity else None

    global_summary = {
        "total_servers": len(server_ids),
        "severity_totals": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
        "by_server": []
    }

    for server_id in server_ids:
        result = get_vuln_severity_distribution(server_id, session, severity_filter)
        server_summary = {
            "server_id": server_id,
            "severity_totals": {k: v["count"] for k, v in result["severities"].items()}
        }
        global_summary["by_server"].append(server_summary)

        for severity, count in server_summary["severity_totals"].items():
            global_summary["severity_totals"][severity] += count

    return global_summary

class TestSession:
    def __init__(self):
        self.query = lambda *args, **kwargs: self
        self.filter = lambda *args, **kwargs: self
        self.all = lambda: [("cve-1",), ("cve-2",)]
        self.in_ = lambda *args: self

def test_get_server_vuln_severity_distribution():
    with patch("app.db.get_session", return_value=TestSession()):
        response = TestClient(router).get("/servers/test-server/vuln_severity_distribution")
        assert response.status_code == 200
        assert "severities" in response.json()
        assert response.json()["total"] >= 0

def test_get_global_vuln_severity_summary():
    with patch("app.db.get_session", return_value=TestSession()):
        response = TestClient(router).get("/servers/vuln_severity_distribution/summary")
        assert response.status_code == 200
        assert "by_server" in response.json()
        assert len(response.json()["by_server"]) > 0

if __name__ == "__main__":
    test_get_server_vuln_severity_distribution()
    test_get_global_vuln_severity_summary()
    print("PASS")