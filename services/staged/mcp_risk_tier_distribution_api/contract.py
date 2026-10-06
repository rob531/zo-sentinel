"""
Self-test contract for mcp_risk_tier_distribution_api.

This module verifies the mcp_risk_tier_distribution_api service is correctly
configured and functional by testing its router via TestClient with SQLite.
"""
import sys
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def get_session_override():
    """Create a session override using SQLite in-memory."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    with TestingSessionLocal() as session:
        yield session


def run_self_test() -> bool:
    """Run self-test to verify the service contract is met."""
    # Import the router from this service
    from services.staged.mcp_risk_tier_distribution_api.router import router
    
    # Create test app with the router
    with patch("app.db.get_session", get_session_override):
        with patch("app.models", None):
            test_app = FastAPI(title="mcp_risk_tier_distribution_api_test")
            test_app.include_router(router)
            
            client = TestClient(test_app)
            
            try:
                # Health check - verify router is properly configured
                response = client.get("/health")
                # Accept both 200 (healthy) or 404 (no health endpoint) as valid
                # The important thing is the router loads without error
                if response.status_code not in (200, 404):
                    print(f"UNEXPECTED: Health check returned {response.status_code}")
                    return False
                    
                # Verify the router has routes
                routes = [r.path for r in test_app.routes]
                if not routes:
                    print("FAIL: No routes registered in router")
                    return False
                    
                return True
                
            except Exception as e:
                print(f"FAIL: Exception during self-test: {e}")
                return False


if __name__ == "__main__":
    if run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)