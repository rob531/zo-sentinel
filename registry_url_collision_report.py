# deps: fastapi, pydantic, sqlalchemy
"""Registry URL Collision Report API.

Provides GET /reporting/url-collisions returning a report of MCP servers
that share the same URL (name+host collision), plus a summary of collision
statistics. This separates genuine scoring-coverage gaps from the
URL-collision artifact.

Read-only. No DB writes.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["url_collisions"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class CollisionGroup(BaseModel):
    url: str
    server_ids: List[str]
    count: int
    has_any_score: bool
    collision_risk: str  # none | partial | full


class CollisionDetail(BaseModel):
    server_id: str
    name: str | None
    url: str
    registry_source: str | None
    risk_tier: str | None
    has_scores: bool
    score_count: int


class CollisionReportResponse(BaseModel):
    total_servers: int
    servers_in_collisions: int
    collision_group_count: int
    full_collision_count: int    # all servers in group lack scores
    partial_collision_count: int  # some servers scored, some not
    collision_groups: List[CollisionGroup]
    collision_details: List[CollisionDetail]


def _latest_model_version(db: Session, server_id: str) -> str | None:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _server_has_scores(db: Session, server_id: str) -> tuple[bool, int]:
    count = db.execute(
        select(func.count(McpLlmAxisScore.id))
        .where(McpLlmAxisScore.server_id == server_id)
    ).scalar() or 0
    return count > 0, count


@router.get("/reporting/url-collisions", response_model=CollisionReportResponse)
def get_url_collision_report(
    registry_source: str | None = Query(None, description="Filter by registry_source"),
    db: Session = Depends(get_session),
) -> CollisionReportResponse:
    """Return URL collision report for MCP servers.

    A URL collision occurs when two or more distinct server_id values
    share the same normalised URL. The report shows which servers are
    involved, how many are unscored, and whether the collision is
    'full' (none scored) or 'partial' (some scored, some not).
    """
    # Build URL -> [server_id] map
    url_map: Dict[str, List[str]] = defaultdict(list)
    all_ids: List[str] = []

    reg_q = select(McpServerRegistry)
    if registry_source:
        reg_q = reg_q.where(McpServerRegistry.registry_source == registry_source)

    for row in db.execute(reg_q).scalars().all():
        if row.url:
            url_map[row.url].append(row.server_id)
            all_ids.append(row.server_id)

    # Filter to colliding groups only
    collision_groups: List[CollisionGroup] = []
    collision_server_ids: set = set()
    full_count = 0
    partial_count = 0

    for url, sids in url_map.items():
        if len(sids) < 2:
            continue
        collision_server_ids.update(sids)
        has_any = False
        all_missing = True
        for sid in sids:
            scored, _ = _server_has_scores(db, sid)
            if scored:
                has_any = True
                all_missing = False
        risk = "partial" if (has_any and not all_missing) else ("full" if all_missing else "none")
        if risk == "full":
            full_count += 1
        elif risk == "partial":
            partial_count += 1
        collision_groups.append(CollisionGroup(
            url=url,
            server_ids=sids,
            count=len(sids),
            has_any_score=has_any,
            collision_risk=risk,
        ))

    # Build detail records for colliding servers
    collision_details: List[CollisionDetail] = []
    for row in db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id.in_(collision_server_ids))
    ).scalars().all():
        scored, score_cnt = _server_has_scores(db, row.server_id)
        collision_details.append(CollisionDetail(
            server_id=row.server_id,
            name=row.name,
            url=row.url or "",
            registry_source=row.registry_source,
            risk_tier=row.risk_tier,
            has_scores=scored,
            score_count=score_cnt,
        ))

    # Sort groups by collision size desc
    collision_groups.sort(key=lambda g: (-g.count, g.url))
    collision_details.sort(key=lambda d: d.server_id)

    return CollisionReportResponse(
        total_servers=len(all_ids),
        servers_in_collisions=len(collision_server_ids),
        collision_group_count=len(collision_groups),
        full_collision_count=full_count,
        partial_collision_count=partial_count,
        collision_groups=collision_groups,
        collision_details=collision_details,
    )


if __name__ == "__main__":  # CI-safe self-test
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
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = SessionLocal()
    # Server A: unscored, shared URL with B
    db.add(McpServerRegistry(server_id="srv_A", name="Server A", url="https://example.com/shared", registry_source="npm", risk_tier="HIGH"))
    # Server B: scored, shared URL with A
    db.add(McpServerRegistry(server_id="srv_B", name="Server B", url="https://example.com/shared", registry_source="npm", risk_tier="MEDIUM"))
    # Server C: unique URL, scored
    db.add(McpServerRegistry(server_id="srv_C", name="Server C", url="https://unique.example.com", registry_source="github", risk_tier="LOW"))
    # Server D: unscored, shared URL with E (full collision)
    db.add(McpServerRegistry(server_id="srv_D", name="Server D", url="https://example.com/both-missing", registry_source="npm", risk_tier=None))
    db.add(McpServerRegistry(server_id="srv_E", name="Server E", url="https://example.com/both-missing", registry_source="npm", risk_tier=None))
    db.commit()

    # Score srv_B and srv_C
    mv = "v3.0_40974559"
    for i, (sid, ax, lbl) in enumerate([
        ("srv_B", "overall_risk", "MEDIUM"),
        ("srv_B", "auth_strength", "STRONG"),
        ("srv_C", "overall_risk", "LOW"),
        ("srv_C", "auth_strength", "WEAK"),
    ], start=1):
        db.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label=lbl, model_version=mv))
    db.commit()
    db.close()

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # Happy path: collision report
    resp = client.get("/api/reporting/url-collisions")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_servers"] == 5
    assert data["servers_in_collisions"] == 4
    assert data["collision_group_count"] == 2
    assert data["full_collision_count"] == 1       # srv_D + srv_E
    assert data["partial_collision_count"] == 1     # srv_A + srv_B
    urls = {g["url"] for g in data["collision_groups"]}
    assert "https://example.com/shared" in urls
    assert "https://example.com/both-missing" in urls

    # Filter by registry_source
    resp2 = client.get("/api/reporting/url-collisions?registry_source=github")
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["total_servers"] == 1
    assert data2["collision_group_count"] == 0

    print("PASS")
