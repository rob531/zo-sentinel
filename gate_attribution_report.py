from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, ServiceHealth
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class ServerSummary(BaseModel):
    server_id: str
    verdict: str
    health_status: Optional[str]
    attribution_timestamp: str

class GateAttributionReport(BaseModel):
    servers: List[ServerSummary]

def get_gate_attribution_report() -> APIRouter:
    @router.get("/gate-attribution/report", response_model=GateAttributionReport)
    async def report(db: Session = Depends(get_session)):
        try:
            # Get MCP server registry data
            servers = db.query(MCPServerRegistry).all()

            server_summaries = []
            for server in servers:
                # Get latest health status from service_health
                health_record = db.query(ServiceHealth).filter(
                    ServiceHealth.server_id == server.server_id
                ).order_by(ServiceHealth.timestamp.desc()).first()

                server_summaries.append(ServerSummary(
                    server_id=server.server_id,
                    verdict=server.verdict,
                    health_status=health_record.status if health_record else None,
                    attribution_timestamp=datetime.utcnow().isoformat()
                ))

            return {"servers": server_summaries}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router

if __name__ == "__main__":
    from app.db import SessionLocal
    from app.models import Base

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test client
    test_app = FastAPI()
    test_app.include_router(get_gate_attribution_report())
    client = TestClient(test_app)

    # Test the endpoint
    response = client.get("/gate-attribution/report")
    assert response.status_code == 200
    assert len(response.json()["servers"]) >= 1
    assert all(key in response.json()["servers"][0] for key in ["server_id", "verdict", "health_status", "attribution_timestamp"])

    print("PASS")