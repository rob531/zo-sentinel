from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Dict

router = APIRouter()

class RiskTierDistribution(BaseModel):
    tier: str
    count: int

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import ServerRegistry

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    # Create tables and seed data for testing
    from mcp_server_registry import Base
    Base.metadata.create_all(bind=engine)

    # Seed data
    from mcp_server_registry import ServerRegistry
    test_data = [
        ServerRegistry(server_id="1", risk_tier="LOW"),
        ServerRegistry(server_id="2", risk_tier="MEDIUM"),
        ServerRegistry(server_id="3", risk_tier="HIGH"),
        ServerRegistry(server_id="4", risk_tier="CRITICAL"),
        ServerRegistry(server_id="5", risk_tier="LOW"),
        ServerRegistry(server_id="6", risk_tier="MEDIUM"),
        ServerRegistry(server_id="7", risk_tier="HIGH"),
        ServerRegistry(server_id="8", risk_tier="CRITICAL"),
        ServerRegistry(server_id="9", risk_tier="LOW"),
        ServerRegistry(server_id="10", risk_tier="MEDIUM"),
        ServerRegistry(server_id="11", risk_tier="HIGH"),
        ServerRegistry(server_id="12", risk_tier="CRITICAL"),
        ServerRegistry(server_id="13", risk_tier="LOW"),
        ServerRegistry(server_id="14", risk_tier="MEDIUM"),
        ServerRegistry(server_id="15", risk_tier="HIGH"),
        ServerRegistry(server_id="16", risk_tier="CRITICAL"),
        ServerRegistry(server_id="17", risk_tier="LOW"),
        ServerRegistry(server_id="18", risk_tier="MEDIUM"),
        ServerRegistry(server_id="19", risk_tier="HIGH"),
        ServerRegistry(server_id="20", risk_tier="CRITICAL"),
        ServerRegistry(server_id="21", risk_tier="LOW"),
        ServerRegistry(server_id="22", risk_tier="MEDIUM"),
        ServerRegistry(server_id="23", risk_tier="HIGH"),
        ServerRegistry(server_id="24", risk_tier="CRITICAL"),
        ServerRegistry(server_id="25", risk_tier="LOW"),
        ServerRegistry(server_id="26", risk_tier="MEDIUM"),
        ServerRegistry(server_id="27", risk_tier="HIGH"),
        ServerRegistry(server_id="28", risk_tier="CRITICAL"),
        ServerRegistry(server_id="29", risk_tier="LOW"),
        ServerRegistry(server_id="30", risk_tier="MEDIUM"),
        ServerRegistry(server_id="31", risk_tier="HIGH"),
        ServerRegistry(server_id="32", risk_tier="CRITICAL"),
        ServerRegistry(server_id="33", risk_tier="LOW"),
        ServerRegistry(server_id="34", risk_tier="MEDIUM"),
        ServerRegistry(server_id="35", risk_tier="HIGH"),
        ServerRegistry(server_id="36", risk_tier="CRITICAL"),
        ServerRegistry(server_id="37", risk_tier="LOW"),
        ServerRegistry(server_id="38", risk_tier="MEDIUM"),
        ServerRegistry(server_id="39", risk_tier="HIGH"),
        ServerRegistry(server_id="40", risk_tier="CRITICAL"),
        ServerRegistry(server_id="41", risk_tier="LOW"),
        ServerRegistry(server_id="42", risk_tier="MEDIUM"),
        ServerRegistry(server_id="43", risk_tier="HIGH"),
        ServerRegistry(server_id="44", risk_tier="CRITICAL"),
        ServerRegistry(server_id="45", risk_tier="LOW"),
        ServerRegistry(server_id="46", risk_tier="MEDIUM"),
        ServerRegistry(server_id="47", risk_tier="HIGH"),
        ServerRegistry(server_id="48", risk_tier="CRITICAL"),
        ServerRegistry(server_id="49", risk_tier="LOW"),
        ServerRegistry(server_id="50", risk_tier="MEDIUM"),
        ServerRegistry(server_id="51", risk_tier="HIGH"),
        ServerRegistry(server_id="52", risk_tier="CRITICAL"),
        ServerRegistry(server_id="53", risk_tier="LOW"),
        ServerRegistry(server_id="54", risk_tier="MEDIUM"),
        ServerRegistry(server_id="55", risk_tier="HIGH"),
        ServerRegistry(server_id="56", risk_tier="CRITICAL"),
        ServerRegistry(server_id="57", risk_tier="LOW"),
        ServerRegistry(server_id="58", risk_tier="MEDIUM"),
        ServerRegistry(server_id="59", risk_tier="HIGH"),
        ServerRegistry(server_id="60", risk_tier="CRITICAL"),
        ServerRegistry(server_id="61", risk_tier="LOW"),
        ServerRegistry(server_id="62", risk_tier="MEDIUM"),
        ServerRegistry(server_id="63", risk_tier="HIGH"),
        ServerRegistry(server_id="64", risk_tier="CRITICAL"),
        ServerRegistry(server_id="65", risk_tier="LOW"),
        ServerRegistry(server_id="66", risk_tier="MEDIUM"),
        ServerRegistry(server_id="67", risk_tier="HIGH"),
        ServerRegistry(server_id="68", risk_tier="CRITICAL"),
        ServerRegistry(server_id="69", risk_tier="LOW"),
        ServerRegistry(server_id="70", risk_tier="MEDIUM"),
        ServerRegistry(server_id="71", risk_tier="HIGH"),
        ServerRegistry(server_id="72", risk_tier="CRITICAL"),
        ServerRegistry(server_id="73", risk_tier="LOW"),
        ServerRegistry(server_id="74", risk_tier="MEDIUM"),
        ServerRegistry(server_id="75", risk_tier="HIGH"),
        ServerRegistry(server_id="76", risk_tier="CRITICAL"),
        ServerRegistry(server_id="77", risk_tier="LOW"),
        ServerRegistry(server_id="78", risk_tier="MEDIUM"),
        ServerRegistry(server_id="79", risk_tier="HIGH"),
        ServerRegistry(server_id="80", risk_tier="CRITICAL"),
        ServerRegistry(server_id="81", risk_tier="LOW"),
        ServerRegistry(server_id="82", risk_tier="MEDIUM"),
        ServerRegistry(server_id="83", risk_tier="HIGH"),
        ServerRegistry(server_id="84", risk_tier="CRITICAL"),
        ServerRegistry(server_id="85", risk_tier="LOW"),
        ServerRegistry(server_id="86", risk_tier="MEDIUM"),
        ServerRegistry(server_id="87", risk_tier="HIGH"),
        ServerRegistry(server_id="88", risk_tier="CRITICAL"),
        ServerRegistry(server_id="89", risk_tier="LOW"),
        ServerRegistry(server_id="90", risk_tier="MEDIUM"),
        ServerRegistry(server_id="91", risk_tier="HIGH"),
        ServerRegistry(server_id="92", risk_tier="CRITICAL"),
        ServerRegistry(server_id="93", risk_tier="LOW"),
        ServerRegistry(server_id="94", risk_tier="MEDIUM"),
        ServerRegistry(server_id="95", risk_tier="HIGH"),
        ServerRegistry(server_id="96", risk_tier="CRITICAL"),
        ServerRegistry(server_id="97", risk_tier="LOW"),
        ServerRegistry(server_id="98", risk_tier="MEDIUM"),
        ServerRegistry(server_id="99", risk_tier="HIGH"),
        ServerRegistry(server_id="100", risk_tier="CRITICAL"),
    ]
    db.add_all(test_data)
    db.commit()

    return db

@router.get("/risk-tier-distribution", response_model=Dict[str, int])
def get_risk_tier_distribution(db: Session = Depends(get_db_session)):
    from mcp_server_registry import ServerRegistry

    # Query the database for risk tier distribution
    query = (
        select(
            ServerRegistry.risk_tier,
            func.count(ServerRegistry.server_id).label("count")
        )
        .group_by(ServerRegistry.risk_tier)
    )

    result = db.execute(query).fetchall()

    # Convert to dictionary
    distribution = {tier: count for tier, count in result}

    # Add the override tier (CRITICAL axis forces the tier)
    distribution["CRITICAL_OVERRIDE"] = distribution.get("CRITICAL", 0)

    return distribution

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/risk-tier-distribution")
    assert response.status_code == 200
    data = response.json()

    # Check all 6 tiers + the override tier are present
    assert set(data.keys()) == {"LOW", "MEDIUM", "HIGH", "CRITICAL", "VERY_HIGH", "VERY_LOW", "CRITICAL_OVERRIDE"}

    print("PASS")