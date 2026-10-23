import requests
from typing import Dict, List, Optional
from fastapi import Depends
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScores,
    McpScoreDisputes,
    Org,
    User
)
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

def get_risk_tier_from_axis_scores(axis_scores: Dict[str, float]) -> str:
    critical_axis = axis_scores.get('critical', 0.0)
    if critical_axis > 0.5:
        return "CRITICAL"
    high_axis = axis_scores.get('high', 0.0)
    if high_axis > 0.7:
        return "HIGH"
    medium_axis = axis_scores.get('medium', 0.0)
    if medium_axis > 0.6:
        return "MEDIUM"
    return "LOW"

def verify_push_consumer_push(server_id: str) -> Dict:
    session: Session = Depends(get_session)()

    try:
        # Get latest axis scores for the server
        latest_axis_scores = session.query(McpLlmAxisScores).filter(
            McpLlmAxisScores.server_id == server_id
        ).order_by(McpLlmAxisScores.created_at.desc()).first()

        if not latest_axis_scores:
            return {
                "passed": False,
                "checks": [],
                "errors": [f"No axis scores found for server {server_id}"]
            }

        axis_scores = {
            'critical': latest_axis_scores.critical,
            'high': latest_axis_scores.high,
            'medium': latest_axis_scores.medium,
            'low': latest_axis_scores.low,
            'info': latest_axis_scores.info,
            'debug': latest_axis_scores.debug,
            'trace': latest_axis_scores.trace
        }

        # Compute expected risk tier
        expected_tier = get_risk_tier_from_axis_scores(axis_scores)

        # Get current risk tier from server registry
        server = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()

        if not server:
            return {
                "passed": False,
                "checks": [],
                "errors": [f"No server registry entry found for {server_id}"]
            }

        actual_tier = server.risk_tier

        # Check if there are any disputes
        disputes = session.query(McpScoreDisputes).filter(
            McpScoreDisputes.server_id == server_id,
            McpScoreDisputes.resolved_at.is_(None)
        ).all()

        dispute_info = []
        if disputes:
            for dispute in disputes:
                dispute_info.append({
                    "id": dispute.id,
                    "reason": dispute.reason,
                    "created_at": dispute.created_at
                })

        # Check scoring wave cost ledger
        try:
            response = requests.post(
                "http://127.0.0.1:8772/query",
                json={
                    "query": f"SELECT * FROM scoring_wave_cost_ledger WHERE server_id = '{server_id}' ORDER BY created_at DESC LIMIT 1",
                    "timeout": 10
                },
                timeout=10
            )
            response.raise_for_status()
            ledger_data = response.json().get("data", [])
            ledger_info = ledger_data[0] if ledger_data else None
        except requests.RequestException as e:
            ledger_info = {"error": str(e)}

        checks = [
            {
                "name": "axis_scores_exist",
                "passed": latest_axis_scores is not None,
                "details": f"Found axis scores for {server_id}"
            },
            {
                "name": "server_registry_exists",
                "passed": server is not None,
                "details": f"Found server registry entry for {server_id}"
            },
            {
                "name": "risk_tier_match",
                "passed": expected_tier == actual_tier,
                "details": f"Expected {expected_tier}, got {actual_tier}",
                "expected": expected_tier,
                "actual": actual_tier
            },
            {
                "name": "no_active_disputes",
                "passed": not disputes,
                "details": f"Found {len(disputes)} active disputes",
                "disputes": dispute_info
            },
            {
                "name": "ledger_data_available",
                "passed": ledger_info is not None and "error" not in ledger_info,
                "details": "Ledger data available" if ledger_info else "No ledger data",
                "ledger": ledger_info
            }
        ]

        passed = all(check["passed"] for check in checks)

        return {
            "passed": passed,
            "checks": checks,
            "errors": [] if passed else [f"Verification failed for server {server_id}"]
        }

    except Exception as e:
        return {
            "passed": False,
            "checks": [],
            "errors": [f"Error verifying server {server_id}: {str(e)}"]
        }
    finally:
        session.close()

def run_verification(sample_size: int = 20) -> Dict:
    session: Session = Depends(get_session)()

    try:
        # Get a sample of servers
        servers = session.query(McpServerRegistry.server_id).limit(sample_size).all()
        server_ids = [server.server_id for server in servers]

        results = []
        for server_id in server_ids:
            result = verify_push_consumer_push(server_id)
            results.append(result)

        passed_count = sum(1 for result in results if result["passed"])
        failed_count = len(results) - passed_count

        return {
            "summary": {
                "total": len(results),
                "passed": passed_count,
                "failed": failed_count
            },
            "results": results
        }

    except Exception as e:
        return {
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0
            },
            "results": [],
            "error": str(e)
        }
    finally:
        session.close()

if __name__ == "__main__":
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Add test data
    session = SessionLocal()
    test_server = McpServerRegistry(
        server_id="test-server-1",
        risk_tier="HIGH"
    )
    session.add(test_server)
    session.commit()

    test_scores = McpLlmAxisScores(
        server_id="test-server-1",
        critical=0.4,
        high=0.8,
        medium=0.5,
        low=0.3,
        info=0.2,
        debug=0.1,
        trace=0.0
    )
    session.add(test_scores)
    session.commit()

    # Run verification
    result = run_verification(sample_size=1)
    if result["summary"]["failed"] == 0:
        print("PASS")
    else:
        print("FAIL")
        for res in result["results"]:
            if not res["passed"]:
                print(f"Server {res['server_id']} failed:")
                for check in res["checks"]:
                    if not check["passed"]:
                        print(f"  - {check['name']}: {check['details']}")
                for error in res["errors"]:
                    print(f"  - Error: {error}")