from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from unittest.mock import Mock

app = FastAPI()

class MCPRiskResponse(BaseModel):
    mcp_name: str
    overall_risk_score: float
    risk_tier: str
    last_assessed_timestamp: datetime

class WriteService:
    def query_top_risky_mcps(self, risk_tier: Optional[str] = None, limit: int = 10) -> List[MCPRiskResponse]:
        # Mock data for testing
        mock_data = [
            MCPRiskResponse(
                mcp_name="MCP1",
                overall_risk_score=95.5,
                risk_tier="HIGH_RISK_ISOLATED",
                last_assessed_timestamp=datetime(2023, 1, 1)
            ),
            MCPRiskResponse(
                mcp_name="MCP2",
                overall_risk_score=85.0,
                risk_tier="KNOWN_THREAT",
                last_assessed_timestamp=datetime(2023, 1, 2)
            ),
            MCPRiskResponse(
                mcp_name="MCP3",
                overall_risk_score=75.5,
                risk_tier="HIGH_RISK_ISOLATED",
                last_assessed_timestamp=datetime(2023, 1, 3)
            ),
            MCPRiskResponse(
                mcp_name="MCP4",
                overall_risk_score=65.0,
                risk_tier="MODERATE_RISK",
                last_assessed_timestamp=datetime(2023, 1, 4)
            ),
        ]

        # Filter by risk tier if provided
        if risk_tier:
            mock_data = [mcp for mcp in mock_data if mcp.risk_tier == risk_tier]

        # Sort by overall_risk_score descending
        mock_data.sort(key=lambda x: x.overall_risk_score, reverse=True)

        # Apply limit
        return mock_data[:limit]

# Dependency injection for write_service
write_service = WriteService()

@app.get("/top_risky_mcps", response_model=List[MCPRiskResponse])
async def get_top_risky_mcps(
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    limit: int = Query(10, description="Limit number of results")
):
    return write_service.query_top_risky_mcps(risk_tier=risk_tier, limit=limit)

if __name__ == '__main__':
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test unfiltered results
    response = client.get("/top_risky_mcps")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4
    assert data[0]["mcp_name"] == "MCP1"
    assert data[0]["overall_risk_score"] == 95.5
    assert data[0]["risk_tier"] == "HIGH_RISK_ISOLATED"

    # Test filtered by risk tier
    response = client.get("/top_risky_mcps?risk_tier=HIGH_RISK_ISOLATED")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(mcp["risk_tier"] == "HIGH_RISK_ISOLATED" for mcp in data)

    # Test limit
    response = client.get("/top_risky_mcps?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    print("PASS")