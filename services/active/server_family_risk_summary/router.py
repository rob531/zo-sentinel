# deps: fastapi, pydantic, sqlalchemy
"""server_family_risk_summary -- per-family aggregate risk stats.

GET /api/families              -- all families with summary
GET /api/families/{family}     -- single family risk detail

Auth: public.  Data: app Postgres (McpServerRegistry, McpLlmAxisScore).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_family_risk_summary"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    tier: str
    count: int


class AxisAvg(BaseModel):
    axis_name: str
    avg_p_top: float | None
    servers_count: int


class FamilySummary(BaseModel):
    family_key: str
    servers_count: int
    tier_distribution: list[TierCount]
    axis_averages: list[AxisAvg]
    worst_p_critical: float | None
    last_scored_at: str | None


class FamiliesListResponse(BaseModel):
    families: list[FamilySummary]
    total_families: int


class FamilyDetailResponse(BaseModel):
    family_key: str
    servers_count: int
    tier_distribution: list[TierCount]
    axis_averages: list[AxisAvg]
    worst_p_critical: float | None
    last_scored_at: str | None
    as_of: str


# --------------------------------------------------------------------------- #
# Family key derivation
# --------------------------------------------------------------------------- #

def derive_family_key(url: Optional[str], name: Optional[str]) -> str:
    """Derive a stable family key from URL host or server name prefix."""
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.netloc.split(":")[0]
            if host.startswith("www."):
                host = host[4:]
            parts = host.split(".")
            if len(parts) >= 2:
                return parts[-2]  # api.github.com -> github, platform.openai.com -> openai
            elif parts:
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


# --------------------------------------------------------------------------- #
# Data access helpers
# --------------------------------------------------------------------------- #

def _build_families(
    db: Session, family_filter: Optional[str] = None
) -> dict[str, dict]:
    """
    Group servers from McpServerRegistry by derived family key.
    Returns {family_key: {"server_ids": set, "tiers": dict, "p_tops": [], "p_criticals": [], "last_scored_at": None}}.
    """
    rows = db.query(McpServerRegistry).filter(McpServerRegistry.url.isnot(None)).all()
    families: dict[str, dict] = {}
    for srv in rows:
        fk = derive_family_key(srv.url, srv.name)
        if family_filter and fk != family_filter:
            continue
        if fk not in families:
            families[fk] = {
                "server_ids": set(),
                "tiers": {},
                "p_tops": [],
                "p_criticals": [],
                "last_scored_at": None,
            }
        families[fk]["server_ids"].add(srv.server_id)
        tier = srv.risk_tier or "unknown"
        families[fk]["tiers"][tier] = families[fk]["tiers"].get(tier, 0) + 1
    return families


def _enrich_with_scores(db: Session, families: dict[str, dict]) -> None:
    """Augment family dicts with axis score aggregates."""
    server_ids = [sid for data in families.values() for sid in data["server_ids"]]
    if not server_ids:
        return

    # Per-server, per-axis aggregates then fold into families
    axis_rows = (
        db.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.max(McpLlmAxisScore.p_critical).label("max_p_critical"),
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id.in_(server_ids))
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .all()
    )

    # family -> axis -> list of per-server avg p_top
    family_axis_p_tops: dict[str, dict[str, list[float]]] = {fk: {} for fk in families}

    for row in axis_rows:
        for fk, data in families.items():
            if row.server_id not in data["server_ids"]:
                continue
            d = family_axis_p_tops.get(fk, {})
            if row.axis_name not in d:
                d[row.axis_name] = []
            if row.avg_p_top is not None:
                d[row.axis_name].append(float(row.avg_p_top))
            if row.max_p_critical is not None:
                pct = float(row.max_p_critical)
                cur = data.get("p_criticals", [])
                if not cur or pct > max(cur):
                    data["p_criticals"] = [pct]
            if row.max_scored_at is not None:
                if data["last_scored_at"] is None or row.max_scored_at > data["last_scored_at"]:
                    data["last_scored_at"] = row.max_scored_at
            family_axis_p_tops[fk] = d
            break

    for fk in families:
        families[fk]["axis_averages"] = family_axis_p_tops.get(fk, {})


def _families_to_model(
    families: dict[str, dict], as_of: str
) -> list[FamilySummary]:
    items = []
    for fk, data in families.items():
        tier_dist = [
            TierCount(tier=t, count=c)
            for t, c in sorted(data.get("tiers", {}).items())
        ]
        axis_averages = []
        for axis_name, p_tops in sorted(data.get("axis_averages", {}).items()):
            avg = round(sum(p_tops) / len(p_tops), 6) if p_tops else None
            axis_averages.append(
                AxisAvg(axis_name=axis_name, avg_p_top=avg, servers_count=len(p_tops))
            )
        pcrits = data.get("p_criticals", [])
        worst = round(max(pcrits), 6) if pcrits else None
        last = data.get("last_scored_at")
        last_str = last.isoformat() if last else None
        items.append(
            FamilySummary(
                family_key=fk,
                servers_count=len(data["server_ids"]),
                tier_distribution=tier_dist,
                axis_averages=axis_averages,
                worst_p_critical=worst,
                last_scored_at=last_str,
            )
        )
    items.sort(key=lambda x: x.family_key)
    return items


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/families", response_model=FamiliesListResponse)
def list_families(
    session: Annotated[Session, Depends(get_session)],
) -> FamiliesListResponse:
    """List all server families with aggregate risk summaries."""
    families = _build_families(session)
    _enrich_with_scores(session, families)
    items = _families_to_model(families, datetime.now(timezone.utc).isoformat())
    return FamiliesListResponse(families=items, total_families=len(items))


@router.get("/families/{family}", response_model=FamilyDetailResponse)
def get_family(
    family: Annotated[str, Path(description="Family key (e.g. 'github', 'openai')")],
    session: Annotated[Session, Depends(get_session)],
) -> FamilyDetailResponse:
    """Risk summary for a specific server family."""
    families = _build_families(session, family_filter=family)
    _enrich_with_scores(session, families)
    items = _families_to_model(families, datetime.now(timezone.utc).isoformat())
    as_of = datetime.now(timezone.utc).isoformat()
    if not items:
        return FamilyDetailResponse(
            family_key=family,
            servers_count=0,
            tier_distribution=[],
            axis_averages=[],
            worst_p_critical=None,
            last_scored_at=None,
            as_of=as_of,
        )
    detail = items[0]
    return FamilyDetailResponse(
        family_key=detail.family_key,
        servers_count=detail.servers_count,
        tier_distribution=detail.tier_distribution,
        axis_averages=detail.axis_averages,
        worst_p_critical=detail.worst_p_critical,
        last_scored_at=detail.last_scored_at,
        as_of=as_of,
    )


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
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        servers = [
            McpServerRegistry(server_id="s1", name="github-mcp", url="https://api.github.com", risk_tier="low", registry_source="test"),
            McpServerRegistry(server_id="s2", name="github-tool", url="https://github.com", risk_tier="medium", registry_source="test"),
            McpServerRegistry(server_id="s3", name="openai-mcp", url="https://api.openai.com", risk_tier="high", registry_source="test"),
            McpServerRegistry(server_id="s4", name="openai-tool", url="https://platform.openai.com", risk_tier="critical", registry_source="test"),
            McpServerRegistry(server_id="s5", name="anon-server", url=None, risk_tier="unknown", registry_source="test"),
        ]
        db.add_all(servers)
        db.flush()
        db.add_all([
            McpLlmAxisScore(id=1, server_id="s1", axis_name="security", label="l", label_index=0, model_version="v1", decision_rule_version="v1", adapter_sha256="a", p_top=0.9, p_critical=0.05, scored_at=now),
            McpLlmAxisScore(id=2, server_id="s1", axis_name="reliability", label="l", label_index=0, model_version="v1", decision_rule_version="v1", adapter_sha256="a", p_top=0.85, p_critical=0.1, scored_at=now),
            McpLlmAxisScore(id=3, server_id="s2", axis_name="security", label="l", label_index=0, model_version="v1", decision_rule_version="v1", adapter_sha256="a", p_top=0.7, p_critical=0.2, scored_at=now),
            McpLlmAxisScore(id=4, server_id="s3", axis_name="security", label="h", label_index=0, model_version="v1", decision_rule_version="v1", adapter_sha256="a", p_top=0.2, p_critical=0.6, scored_at=now),
            McpLlmAxisScore(id=5, server_id="s4", axis_name="security", label="h", label_index=0, model_version="v1", decision_rule_version="v1", adapter_sha256="a", p_top=0.1, p_critical=0.8, scored_at=now),
        ])
        db.commit()

    client = TestClient(that_app)

    # Test list all families
    resp = client.get("/api/families")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["total_families"] == 2, f"Expected 2 families (github+openai), got {data['total_families']}"
    by_key = {f["family_key"]: f for f in data["families"]}

    github = by_key.get("github")
    assert github is not None, "Missing 'github' family"
    assert github["servers_count"] == 2
    tiers = {t["tier"]: t["count"] for t in github["tier_distribution"]}
    assert tiers.get("low") == 1 and tiers.get("medium") == 1, f"github tier dist wrong: {tiers}"

    openai = by_key.get("openai")
    assert openai is not None, "Missing 'openai' family"
    assert openai["servers_count"] == 2
    tiers = {t["tier"]: t["count"] for t in openai["tier_distribution"]}
    assert tiers.get("high") == 1 and tiers.get("critical") == 1, f"openai tier dist wrong: {tiers}"

    # Test single family detail
    resp2 = client.get("/api/families/github")
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}"
    detail = resp2.json()
    assert detail["family_key"] == "github"
    assert detail["servers_count"] == 2
    assert detail["worst_p_critical"] is not None
    sec = next((a for a in detail["axis_averages"] if a["axis_name"] == "security"), None)
    assert sec is not None, "Missing security axis in github family"
    assert sec["servers_count"] == 2

    # Non-existent family returns empty 200
    resp3 = client.get("/api/families/nonexistent")
    assert resp3.status_code == 200
    empty = resp3.json()
    assert empty["servers_count"] == 0

    print("PASS")
    sys.exit(0)
