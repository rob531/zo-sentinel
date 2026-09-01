from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter(prefix="/integration", tags=["integration"])


def get_router_registry() -> List[Dict[str, Any]]:
    """Get registry of all mounted routers."""
    all_routers = []
    
    # Import staged routers
    try:
        from services.staged.server_threat_intel_summary.router import router as r1
        all_routers.append({"name": "server_threat_intel_summary", "router": r1})
    except ImportError:
        pass
    
    try:
        from services.staged.server_verdict.router import router as r2
        all_routers.append({"name": "server_verdict", "router": r2})
    except ImportError:
        pass
    
    try:
        from services.staged.server_verdict_tier_history.router import router as r3
        all_routers.append({"name": "server_verdict_tier_history", "router": r3})
    except ImportError:
        pass
    
    # Import active routers
    try:
        from services.active.axis_change_probe.router import router as r4
        all_routers.append({"name": "axis_change_probe", "router": r4})
    except ImportError:
        pass
    
    try:
        from services.active.axis_score_drift_report.router import router as r5
        all_routers.append({"name": "axis_score_drift_report", "router": r5})
    except ImportError:
        pass
    
    try:
        from services.active.axis_timeline.router import router as r6
        all_routers.append({"name": "axis_timeline", "router": r6})
    except ImportError:
        pass
    
    try:
        from services.active.daemon_roster_health.router import router as r7
        all_routers.append({"name": "daemon_roster_health", "router": r7})
    except ImportError:
        pass
    
    try:
        from services.active.dispute_reason_category_breakdown.router import router as r8
        all_routers.append({"name": "dispute_reason_category_breakdown", "router": r8})
    except ImportError:
        pass
    
    try:
        from services.active.mcp_risk_tier_distribution_dashboard_view_v2.router import router as r9
        all_routers.append({"name": "mcp_risk_tier_distribution_dashboard_view_v2", "router": r9})
    except ImportError:
        pass
    
    try:
        from services.active.McpScoreDispute.router import router as r10
        all_routers.append({"name": "McpScoreDispute", "router": r10})
    except ImportError:
        pass
    
    try:
        from services.active.perspective_event_stream_api.router import router as r11
        all_routers.append({"name": "perspective_event_stream_api", "router": r11})
    except ImportError:
        pass
    
    try:
        from services.active.perspective_snapshot_history.router import router as r12
        all_routers.append({"name": "perspective_snapshot_history", "router": r12})
    except ImportError:
        pass
    
    try:
        from services.active.perspective_snapshot_query_api.router import router as r13
        all_routers.append({"name": "perspective_snapshot_query_api", "router": r13})
    except ImportError:
        pass
    
    try:
        from services.active.registry_search_api.router import router as r14
        all_routers.append({"name": "registry_search_api", "router": r14})
    except ImportError:
        pass
    
    try:
        from services.active.risk_axis_time_series.router import router as r15
        all_routers.append({"name": "risk_axis_time_series", "router": r15})
    except ImportError:
        pass
    
    try:
        from services.active.risk_tier_trend_by_source.router import router as r16
        all_routers.append({"name": "risk_tier_trend_by_source", "router": r16})
    except ImportError:
        pass
    
    try:
        from services.active.score_dispute_report.router import router as r17
        all_routers.append({"name": "score_dispute_report", "router": r17})
    except ImportError:
        pass
    
    return all_routers


@router.get("/status")
def get_integration_status(
    session: Session = Depends(get_session),
    router_registry: List[Dict[str, Any]] = Depends(get_router_registry)
) -> Dict[str, Any]:
    """Get status of integrated routers."""
    return {
        "status": "ok",
        "mounted_routers": len(router_registry),
        "routers": [r["name"] for r in router_registry]
    }


@router.get("/routers")
def list_routers(
    session: Session = Depends(get_session),
    router_registry: List[Dict[str, Any]] = Depends(get_router_registry)
) -> Dict[str, Any]:
    """List all available routers in the registry."""
    return {
        "routers": router_registry,
        "count": len(router_registry)
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE McpServerRegistry (id INTEGER PRIMARY KEY, server_id TEXT, name TEXT)"))
        conn.execute(text("CREATE TABLE McpLlmAxisScore (id INTEGER PRIMARY KEY, server_id TEXT, axis_name TEXT)"))
        conn.execute(text("CREATE TABLE McpScoreDispute (id INTEGER PRIMARY KEY, server_id TEXT, dispute_reason TEXT)"))
        conn.execute(text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, org_id INTEGER, email TEXT)"))
        conn.execute(text("SELECT 1"))
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    from app.db import get_session
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/integration/status")
    
    if response.status_code == 200 and response.json()["status"] == "ok":
        print("PASS")
    else:
        print(f"FAIL: {response.status_code} - {response.text}")