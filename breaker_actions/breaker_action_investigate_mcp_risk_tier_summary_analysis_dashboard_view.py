from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from typing import List, Dict, Any
import requests
import json

app = FastAPI()

def investigate_quarantine_reason(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Investigates the reason for quarantine of mcp_risk_tier_summary_analysis_dashboard_view.py
    by checking related data in the database and ZoComputer store.
    """
    # Check MCPServerRegistry for any issues
    servers = db.query(MCPServerRegistry).all()
    server_issues = []
    for server in servers:
        if server.status == "quarantined":
            server_issues.append({
                "server_id": server.id,
                "status": server.status,
                "reason": server.quarantine_reason
            })

    # Check MCPLLMAxisScores for any anomalies
    scores = db.query(MCPLLMAxisScores).all()
    score_anomalies = []
    for score in scores:
        if score.score < 0 or score.score > 100:
            score_anomalies.append({
                "score_id": score.id,
                "score": score.score,
                "axis": score.axis
            })

    # Check MCPScoreDisputes for any unresolved disputes
    disputes = db.query(MCPScoreDisputes).filter(MCPScoreDisputes.resolved == False).all()
    unresolved_disputes = []
    for dispute in disputes:
        unresolved_disputes.append({
            "dispute_id": dispute.id,
            "reason": dispute.reason,
            "created_at": dispute.created_at
        })

    # Check ZoComputer store for any related issues
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT * FROM mcp_signal_scores WHERE status = 'quarantined'",
                "params": {}
            }
        )
        response.raise_for_status()
        mesh_issues = response.json().get("results", [])
    except requests.RequestException as e:
        mesh_issues = []
        print(f"Error querying ZoComputer store: {e}")

    # Compile findings
    findings = {
        "server_issues": server_issues,
        "score_anomalies": score_anomalies,
        "unresolved_disputes": unresolved_disputes,
        "mesh_issues": mesh_issues
    }

    return findings

@app.get("/investigate-quarantine")
async def investigate_quarantine(db: Session = Depends(get_session)):
    findings = investigate_quarantine_reason(db)
    return {"findings": findings}

if __name__ == "__main__":
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: sessionmaker(bind=engine)()

    # Create tables for testing
    Base.metadata.create_all(engine)

    # Test the investigation function
    test_findings = investigate_quarantine_reason()
    if isinstance(test_findings, dict) and all(key in test_findings for key in ["server_issues", "score_anomalies", "unresolved_disputes", "mesh_issues"]):
        print("PASS")
    else:
        print("FAIL")