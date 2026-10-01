# deps: fastapi, pydantic, sqlalchemy
"""Router for `family_risk_rollup` -- per-family aggregate risk stats.

GET /api/family/risk-rollup
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["family_risk_rollup"])


class TierCount(BaseModel):
    tier: str
    count: int


class FamilyRollupItem(BaseModel):
    family_key: str
    server_count: int
    risk_tier_distribution: list[TierCount]
    mean_p_top: Optional[float]
    max_p_critical: Optional[float]
    last_scored_at: Optional[datetime]


class FamilyRiskRollupResponse(BaseModel):
    families: list[FamilyRollupItem]
    total_families: int


def _derive_family_key(name: Optional[str], url: Optional[str]) -> str:
    """Derive a stable family key from server name or URL domain."""
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            netloc = parsed.netloc.split(":")[0]
            netloc = netloc.lstrip("www.")
            if netloc:
                parts = netloc.split(".")
                return parts[0]
        except Exception:
            pass
    if name:
        lower = name.lower()
        for sep in ("-", "_"):
            if sep in lower:
                return lower.split(sep)[0]
        return lower
    return "unknown"


@router.get("/family/risk-rollup", response_model=FamilyRiskRollupResponse)
def get_family_risk_rollup(
    db: Session = Depends(get_session),
) -> FamilyRiskRollupResponse:
    """
    Roll up server risk statistics by family.

    Groups servers by family (derived from name/URL domain) and aggregates:
      - server count
      - risk_tier distribution
      - mean p_top across servers in the family
      - max p_critical across servers in the family
      - latest scored_at timestamp in the family
    """
    rows = (
        db.query(McpServerRegistry)
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .all()
    )

    families: dict[str, dict] = {}
    for srv in rows:
        fk = _derive_family_key(srv.name, srv.url)
        if fk not in families:
            families[fk] = {
                "server_ids": set(),
                "tier_counts": {},
                "p_tops": [],
                "p_criticals": [],
                "last_scored_at": None,
            }
        families[fk]["server_ids"].add(srv.server_id)
        tier = srv.risk_tier or "unknown"
        families[fk]["tier_counts"][tier] = families[fk]["tier_counts"].get(tier, 0) + 1

    # fetch scores for all servers seen
    server_ids = [sid for data in families.values() for sid in data["server_ids"]]
    if server_ids:
        score_rows = (
            db.query(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.scored_at,
            )
            .filter(McpLlmAxisScore.server_id.in_(server_ids))
            .all()
        )
        for row in score_rows:
            for data in families.values():
                if row.server_id in data["server_ids"]:
                    if row.p_top is not None:
                        data["p_tops"].append(row.p_top)
                    if row.p_critical is not None:
                        data["p_criticals"].append(row.p_critical)
                    if row.scored_at is not None:
                        if data["last_scored_at"] is None or row.scored_at > data["last_scored_at"]:
                            data["last_scored_at"] = row.scored_at
                    break

    items = []
    for fk, data in families.items():
        mean_p_top = round(sum(data["p_tops"]) / len(data["p_tops"]), 6) if data["p_tops"] else None
        max_p_critical = round(max(data["p_criticals"]), 6) if data["p_criticals"] else None
        items.append(
            FamilyRollupItem(
                family_key=fk,
                server_count=len(data["server_ids"]),
                risk_tier_distribution=[
                    TierCount(tier=t, count=c)
                    for t, c in sorted(data["tier_counts"].items())
                ],
                mean_p_top=mean_p_top,
                max_p_critical=max_p_critical,
                last_scored_at=data["last_scored_at"],
            )
        )

    items.sort(key=lambda x: x.family_key)
    return FamilyRiskRollupResponse(families=items, total_families=len(items))


# ---------------------------------------------------------------------------
# Self‑test  (run: python services.active.family_risk_rollup.router)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Override dependency
    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = _override
    app.include_router(router)

    # Seed data
    with TestSession() as db:
        srv1 = McpServerRegistry(
            server_id="s1", name="github-mcp", url="https://api.github.com",
            risk_tier="low", registry_source="test",
        )
        srv2 = McpServerRegistry(
            server_id="s2", name="github-tool", url="https://github.com",
            risk_tier="medium", registry_source="test",
        )
        srv3 = McpServerRegistry(
            server_id="s3", name="openai-mcp", url="https://api.openai.com",
            risk_tier="high", registry_source="test",
        )
        srv4 = McpServerRegistry(
            server_id="s4", name="openai-tool", url="https://platform.openai.com",
            risk_tier="critical", registry_source="test",
        )
        db.add_all([srv1, srv2, srv3, srv4])
        db.flush()
        db.add_all([
            McpLlmAxisScore(
                server_id="s1", axis_name="test", label="test",
                model_version="v1", decision_rule_version="v1", adapter_sha256="a",
                p_top=0.1, p_critical=0.0,
                scored_at=datetime(2024, 1, 1),
            ),
            McpLlmAxisScore(
                server_id="s2", axis_name="test", label="test",
                model_version="v1", decision_rule_version="v1", adapter_sha256="a",
                p_top=0.2, p_critical=0.05,
                scored_at=datetime(2024, 1, 2),
            ),
            McpLlmAxisScore(
                server_id="s3", axis_name="test", label="test",
                model_version="v1", decision_rule_version="v1", adapter_sha256="a",
                p_top=0.8, p_critical=0.3,
                scored_at=datetime(2024, 1, 3),
            ),
            McpLlmAxisScore(
                server_id="s4", axis_name="test", label="test",
                model_version="v1", decision_rule_version="v1", adapter_sha256="a",
                p_top=0.9, p_critical=0.7,
                scored_at=datetime(2024, 1, 4),
            ),
        ])
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/family/risk-rollup")
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["total_families"] == 2, f"Expected 2 families, got {data['total_families']}"
    by_key = {f["family_key"]: f for f in data["families"]}

    github = by_key.get("github")
    assert github is not None, "Missing 'github' family"
    assert github["server_count"] == 2
    tiers = {t["tier"]: t["count"] for t in github["risk_tier_distribution"]}
    assert tiers.get("low") == 1 and tiers.get("medium") == 1

    openai = by_key.get("openai")
    assert openai is not None, "Missing 'openai' family"
    assert openai["server_count"] == 2
    tiers = {t["tier"]: t["count"] for t in openai["risk_tier_distribution"]}
    assert tiers.get("high") == 1 and tiers.get("critical") == 1

    print("PASS")
