from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import List, Optional

router = APIRouter()

class MCPRegistrySummary(BaseModel):
    total_mcps: int
    active_mcps: int
    verdict_tier_counts: dict

def get_db_session() -> Session:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import MCPServerRegistry

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables and seed data
    from sqlalchemy import Column, Integer, String, Boolean
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

    class MCPServerRegistry(Base):
        __tablename__ = 'mcp_server_registry'
        id = Column(Integer, primary_key=True)
        name = Column(String)
        is_active = Column(Boolean)
        verdict_tier = Column(String)

    Base.metadata.create_all(engine)

    # Seed data
    session = SessionLocal()
    session.add_all([
        MCPServerRegistry(name="MCP1", is_active=True, verdict_tier="Tier1"),
        MCPServerRegistry(name="MCP2", is_active=True, verdict_tier="Tier2"),
        MCPServerRegistry(name="MCP3", is_active=False, verdict_tier="Tier1"),
        MCPServerRegistry(name="MCP4", is_active=True, verdict_tier="Tier3"),
        MCPServerRegistry(name="MCP5", is_active=False, verdict_tier="Tier2"),
    ])
    session.commit()

    return session

@router.get("/mcp/registry/summary", response_model=MCPRegistrySummary)
async def get_mcp_registry_summary(db: Session = Depends(get_db_session)):
    from mcp_server_registry import MCPServerRegistry

    # Total MCPs
    total_mcps = db.query(func.count(MCPServerRegistry.id)).scalar()

    # Active MCPs
    active_mcps = db.query(func.count(MCPServerRegistry.id)).filter(MCPServerRegistry.is_active == True).scalar()

    # MCPs by verdict tier
    verdict_tier_counts = db.query(
        MCPServerRegistry.verdict_tier,
        func.count(MCPServerRegistry.id)
    ).group_by(
        MCPServerRegistry.verdict_tier
    ).all()

    verdict_tier_counts = {tier: count for tier, count in verdict_tier_counts}

    return {
        "total_mcps": total_mcps,
        "active_mcps": active_mcps,
        "verdict_tier_counts": verdict_tier_counts
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/mcp/registry/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total_mcps": 5,
        "active_mcps": 3,
        "verdict_tier_counts": {
            "Tier1": 2,
            "Tier2": 2,
            "Tier3": 1
        }
    }

    print("PASS")