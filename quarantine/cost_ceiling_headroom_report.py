import json
from typing import List, Dict, Optional
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
import requests

app = FastAPI()

class CeilingStatus(BaseModel):
    name: str
    ceiling: int
    current: int
    pct_used: float
    state: str

class CeilingReport(BaseModel):
    ceilings: List[CeilingStatus]

def get_write_service_data(query: str) -> Optional[List[Dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def get_rescore_ledger_spend() -> int:
    try:
        with open("data/rescore_ledger.jsonl", "r") as f:
            return sum(int(line.split("|")[1]) for line in f)
    except (FileNotFoundError, ValueError):
        return 0

def get_ceiling_status() -> List[CeilingStatus]:
    session = Depends(get_session)

    # Get registry rows count
    registry_count = session.query(MCPServerRegistry).count()

    # Get perspective count
    perspective_count = session.query(MCPLLMAxisScores).distinct(MCPLLMAxisScores.server_id).count()

    # Get current week's rescore spend
    rescore_spend = get_rescore_ledger_spend()

    # Get CADENCE_REINDEX_MAX_ROWS from write service
    reindex_ceiling_data = get_write_service_data("SELECT value FROM system_config WHERE key = 'CADENCE_REINDEX_MAX_ROWS'")
    reindex_ceiling = reindex_ceiling_data[0]["value"] if reindex_ceiling_data else 1000000

    # Get CADENCE_MAX_PERSPECTIVES from write service
    perspectives_ceiling_data = get_write_service_data("SELECT value FROM system_config WHERE key = 'CADENCE_MAX_PERSPECTIVES'")
    perspectives_ceiling = perspectives_ceiling_data[0]["value"] if perspectives_ceiling_data else 10000

    # Get ZO_RESCORE_WEEKLY_CEILING from write service
    rescore_ceiling_data = get_write_service_data("SELECT value FROM system_config WHERE key = 'ZO_RESCORE_WEEKLY_CEILING'")
    rescore_ceiling = rescore_ceiling_data[0]["value"] if rescore_ceiling_data else 1000000

    ceilings = [
        CeilingStatus(
            name="CADENCE_REINDEX_MAX_ROWS",
            ceiling=reindex_ceiling,
            current=registry_count,
            pct_used=(registry_count / reindex_ceiling) * 100,
            state="OK" if registry_count < reindex_ceiling else "WARN"
        ),
        CeilingStatus(
            name="CADENCE_MAX_PERSPECTIVES",
            ceiling=perspectives_ceiling,
            current=perspective_count,
            pct_used=(perspective_count / perspectives_ceiling) * 100,
            state="OK" if perspective_count < perspectives_ceiling else "WARN"
        ),
        CeilingStatus(
            name="ZO_RESCORE_WEEKLY_CEILING",
            ceiling=rescore_ceiling,
            current=rescore_spend,
            pct_used=(rescore_spend / rescore_ceiling) * 100,
            state="OK" if rescore_spend < rescore_ceiling else "WARN"
        )
    ]

    return ceilings

@app.get("/ceiling-report", response_model=CeilingReport)
async def get_ceiling_report():
    ceilings = get_ceiling_status()
    return CeilingReport(ceilings=ceilings)

if __name__ == "__main__":
    import uvicorn
    from app.db import SessionLocal
    from app.models import Base

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    Base.metadata.create_all(bind=SessionLocal().get_bind())

    # Test data
    test_server = MCPServerRegistry(server_id="test1", hostname="test.example.com")
    test_score = MCPLLMAxisScores(
        server_id="test1",
        overall_risk=0.5,
        auth_strength=0.5,
        capability_breadth=0.5,
        data_sensitivity=0.5,
        network_egress=0.5,
        maintainer_trust=0.5,
        exploit_surface=0.5
    )

    session = SessionLocal()
    session.add(test_server)
    session.add(test_score)
    session.commit()

    # Test the endpoint
    try:
        response = uvicorn.run(app, host="127.0.0.1", port=8000)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")