# deps: fastapi, pydantic, sqlalchemy
"""risk_tier_distribution_by_tool_type_router — risk tier distribution by tool type.

GET /api/risk_tier_distribution  Return risk tier distribution broken down by tool type.

Auth: public.
Data: app tier via get_session + McpServerRegistry.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_distribution_by_tool_type_router"])


# --------------------------------------------------------------------------- #
# Tool-type derivation
# --------------------------------------------------------------------------- #

TOOL_TYPE_PATTERNS: dict[str, list[str]] = {
    "llm": ["llm", "language model", "gpt", "claude", "gemini", "ai assistant", "chatbot"],
    "security": ["security", "vulnerability", "scan", "audit", "penetration", "cve", "advisory"],
    "code": ["code", "repository", "git", "version control", "pull request"],
    "data": ["data", "database", "storage", "analytics", "metrics", "monitoring"],
    "communication": ["notification", "webhook", "email", "slack", "teams", "messaging"],
    "infrastructure": ["infrastructure", "deployment", "kubernetes", "docker", "cloud"],
}


def derive_tool_type(description: Optional[str]) -> str:
    """Classify a server into a tool type based on its description."""
    if not description:
        return "general"
    desc_lower = description.lower()
    for tool_type, patterns in TOOL_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in desc_lower:
                return tool_type
    return "general"


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tier: str
    count: int


class ToolTypeTiers(BaseModel):
    type: str
    tiers: list[TierCount]


class ToolTierDistributionResponse(BaseModel):
    total_servers: int
    tools: list[ToolTypeTiers]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/risk_tier_distribution", response_model=ToolTierDistributionResponse)
def get_risk_tier_distribution(
    db: Session = Depends(get_session),
) -> ToolTierDistributionResponse:
    """Return risk tier distribution broken down by tool type.

    Tool type is derived from each server's description field using keyword
    pattern matching against a predefined taxonomy.
    """
    try:
        rows = (
            db.query(
                McpServerRegistry.risk_tier,
                McpServerRegistry.description,
            )
            .filter(McpServerRegistry.risk_tier.isnot(None))
            .all()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Aggregate in memory
    distribution: dict[str, dict[str, int]] = {}
    total = 0
    for row in rows:
        tool_type = derive_tool_type(row.description)
        tier = row.risk_tier or "unknown"
        distribution.setdefault(tool_type, {})
        distribution[tool_type][tier] = distribution[tool_type].get(tier, 0) + 1
        total += 1

    # Build response
    tools: list[ToolTypeTiers] = []
    for tool_type in sorted(distribution.keys()):
        tiers_dict = distribution[tool_type]
        tiers = [
            TierCount(tier=tier, count=count)
            for tier, count in sorted(tiers_dict.items())
        ]
        tools.append(ToolTypeTiers(type=tool_type, tiers=tiers))

    return ToolTierDistributionResponse(total_servers=total, tools=tools)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestSessionLocal() as db:
        db.add_all([
            McpServerRegistry(
                server_id="s1",
                risk_tier="high",
                description="LLM-based security scanner for vulnerabilities",
            ),
            McpServerRegistry(
                server_id="s2",
                risk_tier="medium",
                description="AI assistant for code review",
            ),
            McpServerRegistry(
                server_id="s3",
                risk_tier="low",
                description="Code repository monitoring tool",
            ),
            McpServerRegistry(
                server_id="s4",
                risk_tier="high",
                description="Language model API gateway",
            ),
            McpServerRegistry(
                server_id="s5",
                risk_tier="medium",
                description="Database analytics platform",
            ),
            McpServerRegistry(
                server_id="s6",
                risk_tier="low",
                description="Git webhook notification service",
            ),
        ])
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/risk_tier_distribution")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()

    assert "tools" in data, "Response missing 'tools'"
    assert data["total_servers"] == 6, f"Expected 6, got {data['total_servers']}"

    tool_types = {t["type"] for t in data["tools"]}
    assert "llm" in tool_types, f"Expected 'llm' in {tool_types}"
    assert "code" in tool_types, f"Expected 'code' in {tool_types}"

    # Verify llm tool has correct tier counts
    llm_tool = next(t for t in data["tools"] if t["type"] == "llm")
    llm_tiers = {t["tier"]: t["count"] for t in llm_tool["tiers"]}
    assert llm_tiers.get("high", 0) == 2, f"Expected high=2, got {llm_tiers}"
    assert llm_tiers.get("medium", 0) == 1, f"Expected medium=1, got {llm_tiers}"

    print("PASS")
