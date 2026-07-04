from fastapi import Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from app.routers import app_router_registry
from app.views.mcp_risk_tier_distribution_analysis_dashboard_view import mcp_risk_tier_distribution_analysis_dashboard_view

app_router_registry.register(mcp_risk_tier_distribution_analysis_dashboard_view)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Verify the view is registered
    assert mcp_risk_tier_distribution_analysis_dashboard_view in app_router_registry.views, "View not registered"
    print("PASS")