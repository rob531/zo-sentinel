from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
from pydantic import BaseModel

app = FastAPI()

class SignalScore(BaseModel):
    server_id: int
    signal_name: str
    score: float
    timestamp: str

class MeshMemory(BaseModel):
    server_id: int
    memory: Dict[str, str]

class ServerRiskTier(BaseModel):
    server_id: int
    risk_tier: int

class VerdictBreakdown(BaseModel):
    server_id: int
    verdict: str
    count: int

class VerdictHistory(BaseModel):
    server_id: int
    verdict: str
    timestamp: str

class VulnAdvisory(BaseModel):
    server_id: int
    advisory_id: str
    severity: str
    description: str

class FamilyCoverage(BaseModel):
    server_id: int
    family: str
    coverage: float

class ScoringPrecisionAudit(BaseModel):
    server_id: int
    signal_name: str
    precision: float

class TrustOverrideDiscrepancy(BaseModel):
    server_id: int
    override: bool
    discrepancy: bool

class ServerThreatIntel(BaseModel):
    server_id: int
    threat_intel: Dict[str, str]

class RiskDistributionSummary(BaseModel):
    risk_tier: int
    count: int

class RegistryGrowthProgress(BaseModel):
    date: str
    count: int

class ScoringFreshnessDashboard(BaseModel):
    server_id: int
    freshness: float

class ServerAxisScores(BaseModel):
    server_id: int
    axis: str
    score: float

class ServerRiskDetail(BaseModel):
    server_id: int
    risk_detail: Dict[str, str]

class OverviewDashboard(BaseModel):
    server_id: int
    overview: Dict[str, str]

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> List[SignalScore]:
    """Get signal scores for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [SignalScore(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> MeshMemory:
    """Get mesh memory for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return MeshMemory(**response.json()[0])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_server_risk_tier(server_id: int, session: Session = Depends(get_session)) -> ServerRiskTier:
    """Get risk tier for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM McpServerRegistry WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return ServerRiskTier(**response.json()[0])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_verdict_breakdown(server_id: int, session: Session = Depends(get_session)) -> List[VerdictBreakdown]:
    """Get verdict breakdown for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_verdict_breakdown WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [VerdictBreakdown(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_verdict_history(server_id: int, session: Session = Depends(get_session)) -> List[VerdictHistory]:
    """Get verdict history for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_verdict_history WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [VerdictHistory(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_vuln_advisories(server_id: int, session: Session = Depends(get_session)) -> List[VulnAdvisory]:
    """Get vulnerability advisories for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_vuln_advisories WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [VulnAdvisory(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_family_coverage(server_id: int, session: Session = Depends(get_session)) -> List[FamilyCoverage]:
    """Get family coverage for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_family_coverage WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [FamilyCoverage(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_scoring_precision_audit(server_id: int, session: Session = Depends(get_session)) -> List[ScoringPrecisionAudit]:
    """Get scoring precision audit for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_scoring_precision_audit WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [ScoringPrecisionAudit(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_trust_override_discrepancy(server_id: int, session: Session = Depends(get_session)) -> List[TrustOverrideDiscrepancy]:
    """Get trust override discrepancy for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_trust_override_discrepancy WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [TrustOverrideDiscrepancy(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_server_threat_intel(server_id: int, session: Session = Depends(get_session)) -> List[ServerThreatIntel]:
    """Get server threat intel for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_server_threat_intel WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [ServerThreatIntel(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_risk_distribution_summary(session: Session = Depends(get_session)) -> List[RiskDistributionSummary]:
    """Get risk distribution summary."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT risk_tier, COUNT(*) as count FROM McpServerRegistry GROUP BY risk_tier"}
        )
        response.raise_for_status()
        return [RiskDistributionSummary(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_registry_growth_progress(session: Session = Depends(get_session)) -> List[RegistryGrowthProgress]:
    """Get registry growth progress."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT date, COUNT(*) as count FROM McpServerRegistry GROUP BY date"}
        )
        response.raise_for_status()
        return [RegistryGrowthProgress(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_scoring_freshness_dashboard(session: Session = Depends(get_session)) -> List[ScoringFreshnessDashboard]:
    """Get scoring freshness dashboard."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT server_id, freshness FROM mcp_scoring_freshness_dashboard"}
        )
        response.raise_for_status()
        return [ScoringFreshnessDashboard(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_server_axis_scores(server_id: int, session: Session = Depends(get_session)) -> List[ServerAxisScores]:
    """Get server axis scores for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM McpLlmAxisScore WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [ServerAxisScores(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_server_risk_detail(server_id: int, session: Session = Depends(get_session)) -> List[ServerRiskDetail]:
    """Get server risk detail for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_server_risk_detail WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [ServerRiskDetail(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_overview_dashboard(server_id: int, session: Session = Depends(get_session)) -> List[OverviewDashboard]:
    """Get overview dashboard for a server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_overview_dashboard WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return [OverviewDashboard(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def _run_self_test():
    """Self-test for the module."""
    from app.dependency_overrides import override_get_session
    from app.db import get_session
    from app.models import Base

    # Override the session for testing
    override_get_session()

    # Create a test database
    from sqlalchemy import create_engine
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)

    # Test the functions
    try:
        # Test get_signal_scores
        signal_scores = get_signal_scores(1)
        assert isinstance(signal_scores, list)

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory(1)
        assert isinstance(mesh_memory, MeshMemory)

        # Test get_server_risk_tier
        server_risk_tier = get_server_risk_tier(1)
        assert isinstance(server_risk_tier, ServerRiskTier)

        # Test get_verdict_breakdown
        verdict_breakdown = get_verdict_breakdown(1)
        assert isinstance(verdict_breakdown, list)

        # Test get_verdict_history
        verdict_history = get_verdict_history(1)
        assert isinstance(verdict_history, list)

        # Test get_vuln_advisories
        vuln_advisories = get_vuln_advisories(1)
        assert isinstance(vuln_advisories, list)

        # Test get_family_coverage
        family_coverage = get_family_coverage(1)
        assert isinstance(family_coverage, list)

        # Test get_scoring_precision_audit
        scoring_precision_audit = get_scoring_precision_audit(1)
        assert isinstance(scoring_precision_audit, list)

        # Test get_trust_override_discrepancy
        trust_override_discrepancy = get_trust_override_discrepancy(1)
        assert isinstance(trust_override_discrepancy, list)

        # Test get_server_threat_intel
        server_threat_intel = get_server_threat_intel(1)
        assert isinstance(server_threat_intel, list)

        # Test get_risk_distribution_summary
        risk_distribution_summary = get_risk_distribution_summary()
        assert isinstance(risk_distribution_summary, list)

        # Test get_registry_growth_progress
        registry_growth_progress = get_registry_growth_progress()
        assert isinstance(registry_growth_progress, list)

        # Test get_scoring_freshness_dashboard
        scoring_freshness_dashboard = get_scoring_freshness_dashboard()
        assert isinstance(scoring_freshness_dashboard, list)

        # Test get_server_axis_scores
        server_axis_scores = get_server_axis_scores(1)
        assert isinstance(server_axis_scores, list)

        # Test get_server_risk_detail
        server_risk_detail = get_server_risk_detail(1)
        assert isinstance(server_risk_detail, list)

        # Test get_overview_dashboard
        overview_dashboard = get_overview_dashboard(1)
        assert isinstance(overview_dashboard, list)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    _run_self_test()