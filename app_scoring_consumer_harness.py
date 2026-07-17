import requests
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from fastapi import Depends
from sqlalchemy.orm import Session
from app.trust_gating_override import override_trust_gate

def run_harness() -> Dict[str, Any]:
    results = []
    base_url = "http://127.0.0.1:8772"

    # Test 1: Server with CRITICAL auth_strength axis should be overridden to HIGH_RISK_ISOLATED
    test_name = "CRITICAL auth_strength override"
    try:
        # Seed test data
        server_id = 1
        org_id = 1
        user_id = 1

        # Create test org and user
        requests.post(f"{base_url}/query", json={
            "query": f"INSERT INTO orgs (id, name) VALUES ({org_id}, 'Test Org')"
        })
        requests.post(f"{base_url}/query", json={
            "query": f"INSERT INTO users (id, name, org_id) VALUES ({user_id}, 'Test User', {org_id})"
        })

        # Create test server with CRITICAL auth_strength
        requests.post(f"{base_url}/query", json={
            "query": f"""
            INSERT INTO mcp_server_registry (id, org_id, name, auth_strength, overall_risk)
            VALUES ({server_id}, {org_id}, 'Test Server', 'CRITICAL', 'MEDIUM')
            """
        })

        # Add axis scores
        requests.post(f"{base_url}/query", json={
            "query": f"""
            INSERT INTO mcp_llm_axis_scores (server_id, axis, score, timestamp)
            VALUES
            ({server_id}, 'auth_strength', 0.1, NOW()),
            ({server_id}, 'data_sensitivity', 0.8, NOW()),
            ({server_id}, 'compliance', 0.9, NOW())
            """
        })

        # Run the scoring logic (simulated by calling the override function directly)
        with Depends(get_session) as session:
            server = session.query(MCPServerRegistry).filter_by(id=server_id).first()
            override_trust_gate(server)

            # Verify the override
            updated_server = session.query(MCPServerRegistry).filter_by(id=server_id).first()
            if updated_server.risk_tier == "HIGH_RISK_ISOLATED" and updated_server.overall_risk == "HIGH":
                results.append({"name": test_name, "passed": True, "detail": "Override applied correctly"})
            else:
                results.append({"name": test_name, "passed": False, "detail": f"Expected HIGH_RISK_ISOLATED, got {updated_server.risk_tier}"})

    except Exception as e:
        results.append({"name": test_name, "passed": False, "detail": str(e)})

    # Test 2: Fully-positive server should get TRUSTED_GENERAL
    test_name = "Fully-positive server"
    try:
        # Seed test data
        server_id = 2
        org_id = 2
        user_id = 2

        # Create test org and user
        requests.post(f"{base_url}/query", json={
            "query": f"INSERT INTO orgs (id, name) VALUES ({org_id}, 'Test Org 2')"
        })
        requests.post(f"{base_url}/query", json={
            "query": f"INSERT INTO users (id, name, org_id) VALUES ({user_id}, 'Test User 2', {org_id})"
        })

        # Create test server with all positive scores
        requests.post(f"{base_url}/query", json={
            "query": f"""
            INSERT INTO mcp_server_registry (id, org_id, name, auth_strength, overall_risk)
            VALUES ({server_id}, {org_id}, 'Test Server 2', 'LOW', 'LOW')
            """
        })

        # Add axis scores
        requests.post(f"{base_url}/query", json={
            "query": f"""
            INSERT INTO mcp_llm_axis_scores (server_id, axis, score, timestamp)
            VALUES
            ({server_id}, 'auth_strength', 0.9, NOW()),
            ({server_id}, 'data_sensitivity', 0.9, NOW()),
            ({server_id}, 'compliance', 0.9, NOW())
            """
        })

        # Run the scoring logic (simulated by calling the override function directly)
        with Depends(get_session) as session:
            server = session.query(MCPServerRegistry).filter_by(id=server_id).first()
            override_trust_gate(server)

            # Verify the override
            updated_server = session.query(MCPServerRegistry).filter_by(id=server_id).first()
            if updated_server.risk_tier == "TRUSTED_GENERAL" and updated_server.overall_risk == "LOW":
                results.append({"name": test_name, "passed": True, "detail": "Correctly assigned TRUSTED_GENERAL"})
            else:
                results.append({"name": test_name, "passed": False, "detail": f"Expected TRUSTED_GENERAL, got {updated_server.risk_tier}"})

    except Exception as e:
        results.append({"name": test_name, "passed": False, "detail": str(e)})

    # Determine overall pass/fail
    passed = all(result["passed"] for result in results)

    return {
        "passed": passed,
        "tests": results
    }

if __name__ == '__main__':
    result = run_harness()
    print('PASS' if result['passed'] else 'FAIL')
    assert result['passed']