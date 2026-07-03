from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def get_mcp_risk_tier_trend_data(session: Session = Depends(get_session)) -> List[Dict]:
    try:
        # Query MCPServerRegistry for relevant servers
        servers = session.query(MCPServerRegistry).all()

        # Query MCPLLMAxisScores for risk tier trends
        risk_scores = session.query(MCPLLMAxisScores).all()

        # Combine data
        trend_data = []
        for server in servers:
            server_data = {
                "server_id": server.id,
                "server_name": server.name,
                "risk_tiers": []
            }
            for score in risk_scores:
                if score.server_id == server.id:
                    server_data["risk_tiers"].append({
                        "timestamp": score.timestamp,
                        "risk_tier": score.risk_tier
                    })
            trend_data.append(server_data)

        return trend_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def investigate_mcp_risk_tier_trend_dashboard_view() -> str:
    try:
        # Get data from local database
        session = get_session()
        trend_data = get_mcp_risk_tier_trend_data(session)

        # Analyze trends
        analysis = []
        for server_data in trend_data:
            if not server_data["risk_tiers"]:
                continue

            # Simple trend analysis
            current_tier = server_data["risk_tiers"][-1]["risk_tier"]
            previous_tier = server_data["risk_tiers"][0]["risk_tier"] if len(server_data["risk_tiers"]) > 1 else current_tier

            if current_tier > previous_tier:
                trend = "increasing"
            elif current_tier < previous_tier:
                trend = "decreasing"
            else:
                trend = "stable"

            analysis.append({
                "server_id": server_data["server_id"],
                "server_name": server_data["server_name"],
                "current_risk_tier": current_tier,
                "trend": trend
            })

        # Generate report
        report = {
            "analysis": analysis,
            "recommendation": "Further investigation needed for servers with increasing risk tiers."
        }

        return json.dumps(report, indent=2)

    except Exception as e:
        return f"Investigation failed: {str(e)}"

if __name__ == "__main__":
    # Self-test
    try:
        report = investigate_mcp_risk_tier_trend_dashboard_view()
        print(report)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")