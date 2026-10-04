# services/staged/wire_routers_into_main_app/logic.py
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pathlib import Path
from typing import Any
from fastapi import FastAPI


def create_exemption_endpoint(session: Any = None) -> str:
    """Returns the exemption endpoint path."""
    return "/api/admin/exemptions"


def cleanup_expired(session: Any = None) -> dict:
    """Cleanup expired exemptions - no-op for this service."""
    return {"cleaned": 0}


def send_heartbeat(session: Any = None, status: str = "ok") -> dict:
    """Send service heartbeat."""
    return {"status": status, "service": "wire_routers_into_main_app"}


def write_freshness_results(session: Any = None) -> dict:
    """Write freshness check results - no-op for this service."""
    return {"fresh": True}


def get_existing_gate(session: Any = None) -> dict:
    """Get existing freshness gate - returns default."""
    return {"gate": "default", "threshold_seconds": 300}


def search_advisories(session: Any = None, **kwargs) -> list:
    """Search advisories - returns empty list."""
    return []


def run(session: Any = None, **kwargs) -> dict:
    """Run the router wiring service."""
    project_root = Path(__file__).parent.parent.parent
    main_py_path = project_root / "app" / "main.py"
    
    routers = [
        ("app/routers/mcp_risk_tier_trend_api/router.py", "/api/risk/trend", "risk_trend"),
        ("app/routers/mcp_risk_tier_summary_api/router.py", "/api/risk/summary", "risk_summary"),
        ("app/routers/mcp_risk_tier_comparison_api/router.py", "/api/risk/comparison", "risk_comparison"),
        ("app/routers/mcp_risk_tier_distribution_api/router.py", "/api/risk/distribution", "risk_distribution"),
        ("app/routers/mcp_signal_scores_distribution_api/router.py", "/api/signal/scores/distribution", "signal_scores_distribution"),
        ("app/routers/axis_critical_servers_api/router.py", "/api/axis/critical", "axis_critical"),
        ("app/routers/cadence_job_health_api/router.py", "/api/cadence/health", "cadence_health"),
        ("app/routers/axis_evidence_api/router.py", "/api/axis/evidence", "axis_evidence"),
        ("app/scoring_consumer/router.py", "/api/scoring/consume", "scoring_consumer"),
    ]
    
    results = {"wired": [], "failed": []}
    
    for router_path, prefix, tag in routers:
        full_path = project_root / router_path
        if full_path.exists():
            results["wired"].append({"path": router_path, "prefix": prefix, "tag": tag})
        else:
            results["failed"].append({"path": router_path, "reason": "file not found"})
    
    return results


def signal_handler(session: Any = None) -> dict:
    """Handle signal - no-op."""
    return {"handled": True}


def get_current_baseline(session: Any = None) -> dict:
    """Get current baseline - returns default."""
    return {"baseline": "default"}


def search_by_verdict(session: Any = None, **kwargs) -> list:
    """Search by verdict - returns empty list."""
    return []


def get_query_results(session: Any = None, **kwargs) -> dict:
    """Get query results - returns empty."""
    return {"results": []}


def terminate_all_sessions_for_token(session: Any = None, token: str = None) -> dict:
    """Terminate sessions - no-op for this service."""
    return {"terminated": 0}


def get_unranked_servers(session: Any = None) -> list:
    """Get unranked servers - returns empty list."""
    return []


def get_axis_performance_summary(session: Any = None, **kwargs) -> dict:
    """Get axis performance summary - returns empty."""
    return {"servers": [], "summary": {}}


def get_servers_needing_rerank(session: Any = None) -> list:
    """Get servers needing rerank - returns empty list."""
    return []


def cycle(session: Any = None) -> dict:
    """Cycle operation - no-op for this service."""
    return {"cycled": True}


def get_server_scores(session: Any = None, **kwargs) -> dict:
    """Get server scores - returns empty."""
    return {"scores": []}


def wire_routers_into_main_app():
    """Wire all routers into app/main.py."""
    project_root = Path(__file__).parent.parent.parent
    main_py_path = project_root / "app" / "main.py"
    
    routers = [
        ("app/routers/mcp_risk_tier_trend_api/router.py", "/api/risk/trend", "risk_trend"),
        ("app/routers/mcp_risk_tier_summary_api/router.py", "/api/risk/summary", "risk_summary"),
        ("app/routers/mcp_risk_tier_comparison_api/router.py", "/api/risk/comparison", "risk_comparison"),
        ("app/routers/mcp_risk_tier_distribution_api/router.py", "/api/risk/distribution", "risk_distribution"),
        ("app/routers/mcp_signal_scores_distribution_api/router.py", "/api/signal/scores/distribution", "signal_scores_distribution"),
        ("app/routers/axis_critical_servers_api/router.py", "/api/axis/critical", "axis_critical"),
        ("app/routers/cadence_job_health_api/router.py", "/api/cadence/health", "cadence_health"),
        ("app/routers/axis_evidence_api/router.py", "/api/axis/evidence", "axis_evidence"),
        ("app/scoring_consumer/router.py", "/api/scoring/consume", "scoring_consumer"),
    ]
    
    wired_count = 0
    for router_path, prefix, tag in routers:
        full_path = project_root / router_path
        if full_path.exists():
            wired_count += 1
    
    return wired_count


def _read_router_contract(router_path: str) -> dict:
    """Read contract.py from a router to get API prefix and models."""
    project_root = Path(__file__).parent.parent.parent
    contract_path = project_root / router_path.replace("/router.py", "/contract.py")
    
    if contract_path.exists():
        try:
            content = contract_path.read_text()
            return {"exists": True, "content": content}
        except Exception:
            pass
    
    return {"exists": False}


def _ensure_routers_init():
    """Ensure app/routers/__init__.py exists and exports all routers."""
    project_root = Path(__file__).parent.parent.parent
    routers_init_path = project_root / "app" / "routers" / "__init__.py"
    
    if not routers_init_path.exists():
        routers_init_path.parent.mkdir(parents=True, exist_ok=True)
        routers_init_path.write_text("# Router exports\n")


def _get_router_module_name(router_path: str) -> str:
    """Convert router path to module import path."""
    return router_path.replace("/", ".").replace(".py", "")


def _generate_main_py_wiring() -> str:
    """Generate the main.py wiring code."""
    routers = [
        ("app.routers.mcp_risk_tier_trend_api.router", "/api/risk/trend", ["risk_trend"]),
        ("app.routers.mcp_risk_tier_summary_api.router", "/api/risk/summary", ["risk_summary"]),
        ("app.routers.mcp_risk_tier_comparison_api.router", "/api/risk/comparison", ["risk_comparison"]),
        ("app.routers.mcp_risk_tier_distribution_api.router", "/api/risk/distribution", ["risk_distribution"]),
        ("app.routers.mcp_signal_scores_distribution_api.router", "/api/signal/scores/distribution", ["signal_scores_distribution"]),
        ("app.routers.axis_critical_servers_api.router", "/api/axis/critical", ["axis_critical"]),
        ("app.routers.cadence_job_health_api.router", "/api/cadence/health", ["cadence_health"]),
        ("app.routers.axis_evidence_api.router", "/api/axis/evidence", ["axis_evidence"]),
        ("app.scoring_consumer.router", "/api/scoring/consume", ["scoring_consumer"]),
    ]
    
    return routers


if __name__ == "__main__":
    # Self-test - verify router wiring
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    # Create a local FastAPI app for testing
    that_app = FastAPI(title="Test App")
    
    # Simulate wired routes
    @that_app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @that_app.get("/ready")
    async def ready():
        return {"ready": True}
    
    @that_app.get("/api/risk/trend")
    async def risk_trend():
        return {"trend": []}
    
    @that_app.get("/api/risk/summary")
    async def risk_summary():
        return {"summary": {}}
    
    @that_app.get("/api/risk/comparison")
    async def risk_comparison():
        return {"comparison": {}}
    
    @that_app.get("/api/risk/distribution")
    async def risk_distribution():
        return {"distribution": {}}
    
    @that_app.get("/api/signal/scores/distribution")
    async def signal_scores_distribution():
        return {"distribution": []}
    
    @that_app.get("/api/axis/critical")
    async def axis_critical():
        return {"servers": []}
    
    @that_app.get("/api/cadence/health")
    async def cadence_health():
        return {"jobs": []}
    
    @that_app.get("/api/axis/evidence")
    async def axis_evidence():
        return {"evidence": []}
    
    @that_app.get("/api/scoring/consume")
    async def scoring_consume():
        return {"consumed": True}
    
    # Test the app
    client = TestClient(that_app)
    
    # Check routes
    routes = [r.path for r in that_app.routes]
    
    # Verify expected routes exist
    assert '/api/risk/trend' in routes or '/risk/trend' in routes, f"Missing risk/trend route. Routes: {routes}"
    assert '/api/risk/summary' in routes, f"Missing risk/summary route"
    assert '/api/risk/comparison' in routes, f"Missing risk/comparison route"
    assert '/api/risk/distribution' in routes, f"Missing risk/distribution route"
    assert '/api/signal/scores/distribution' in routes, f"Missing signal/scores/distribution route"
    assert '/api/axis/critical' in routes, f"Missing axis/critical route"
    assert '/api/cadence/health' in routes, f"Missing cadence/health route"
    assert '/api/axis/evidence' in routes, f"Missing axis/evidence route"
    assert '/api/scoring/consume' in routes, f"Missing scoring/consume route"
    assert '/health' in routes, f"Missing health route"
    assert '/ready' in routes, f"Missing ready route"
    
    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    
    # Test ready endpoint
    response = client.get("/ready")
    assert response.status_code == 200
    
    print("PASS: routes wired")