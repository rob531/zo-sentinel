# deps: fastapi, sqlalchemy, pydantic
"""scoring_gap_api -- surfaced servers that have never been scored.

GET /api/scoring/gap
    Returns scoring-gap metrics: total registered servers, count with ≥1
    axis score, count without any axis score, and a sample of unscored
    servers (up to 100 rows).

Auth: public.  Data: app Postgres via get_session + McpServerRegistry
+ McpLlmAxisScore.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

# Ensure repo root is on path so `from app.db` resolves correctly
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_gap_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class UnscoredServerSample(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    name: str = Field(..., description="Server display name")
    first_seen: datetime = Field(..., description="When the server was first registered")
    registry_source: str = Field(..., description="Origin of the registration")

    model_config = {"from_attributes": True}


class ScoringGapResponse(BaseModel):
    total_servers: int = Field(..., description="Total servers in mcp_server_registry")
    scored_count: int = Field(..., description="Servers that have at least one axis score")
    unscored_count: int = Field(..., description="Servers with no axis scores")
    unscored_sample: List[UnscoredServerSample] = Field(
        default_factory=list,
        description="Up to 100 unscored servers with details",
    )

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get(
    "/scoring/gap",
    response_model=ScoringGapResponse,
    summary="Scoring gap — how many registered servers lack axis scores",
    responses={200: {"description": "Gap metrics with unscored server sample"}},
)
def get_scoring_gap(
    db: Session = Depends(get_session),
) -> ScoringGapResponse:
    """
    Compares mcp_server_registry against mcp_llm_axis_scores to surface the
    population of servers that have been registered but never scored.
    """
    # Total registered servers
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Distinct server_ids that appear in the axis-scores table
    scored_subq = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    scored_count = (
        db.query(func.count(scored_subq.c.server_id))
        .scalar()
    ) or 0

    unscored_count = total_servers - scored_count

    # Sample of unscored servers (LEFT OUTER JOIN)
    unscored_rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.first_seen,
            McpServerRegistry.registry_source,
        )
        .outerjoin(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(McpLlmAxisScore.id.is_(None))
        .limit(100)
    ).all()

    unscored_sample = [
        UnscoredServerSample(
            server_id=row[0],
            name=row[1] or "",
            first_seen=row[2] or datetime.now(timezone.utc),
            registry_source=row[3] or "",
        )
        for row in unscored_rows
    ]

    return ScoringGapResponse(
        total_servers=total_servers,
        scored_count=scored_count,
        unscored_count=unscored_count,
        unscored_sample=unscored_sample,
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
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Seed via raw SQL: meta is TEXT (schema: meta → column "metadata", Text),
    # probs is JSON (stored as TEXT in SQLite, needs json.dumps)
    with TestSession() as db:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text("""
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, url, description,
                     trust_score, verdict, verdict_reasoning, confidence,
                     last_assessed, first_seen, last_seen, last_scanned,
                     scan_count, risk_tier, metadata)
                VALUES
                    (:sid, :name, :src, :url, '', 0.5, '', '', 0.0,
                     :now, '2024-01-15', :now, :now, 1, 'LOW', ''),
                    (:sid2, :name2, :src2, :url2, '', 0.5, '', '', 0.0,
                     :now, '2024-02-20', :now, :now, 0, 'LOW', ''),
                    (:sid3, :name3, :src3, :url3, '', 0.5, '', '', 0.0,
                     :now, '2024-03-10', :now, :now, 0, 'LOW', '')
            """),
            {
                "sid": "srv-001", "name": "Server Alpha",
                "src": "github", "url": "https://example.com/alpha",
                "sid2": "srv-002", "name2": "Server Beta",
                "src2": "npm", "url2": "https://example.com/beta",
                "sid3": "srv-003", "name3": "Server Gamma",
                "src3": "docker", "url3": "https://example.com/gamma",
                "now": now,
            },
        )
        db.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index,
                     model_version, decision_rule_version, probs,
                     p_top, p_critical, p_danger, escalated,
                     scored_at, adapter_sha256, escalated_to)
                VALUES
                    (:sid, 'overall_risk', 'LOW', 0,
                     'v1', 'r1', :probs,
                     0.9, 0.0, 0.05, 0,
                     :scored, :sha, NULL)
            """),
            {
                "sid": "srv-001",
                "probs": json.dumps({"LOW": 0.9, "MEDIUM": 0.1}),
                "scored": now,
                "sha": "a" * 64,
            },
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    resp = client.get("/api/scoring/gap")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("scored_count") != 1:
        print(f"FAIL: scored_count expected 1, got {data.get('scored_count')}", file=sys.stderr)
        sys.exit(1)
    if data.get("unscored_count") != 2:
        print(f"FAIL: unscored_count expected 2, got {data.get('unscored_count')}", file=sys.stderr)
        sys.exit(1)
    if data.get("total_servers") != 3:
        print(f"FAIL: total_servers expected 3, got {data.get('total_servers')}", file=sys.stderr)
        sys.exit(1)
    if len(data.get("unscored_sample", [])) != 2:
        print(f"FAIL: unscored_sample expected 2 rows, got {len(data.get('unscored_sample', []))}", file=sys.stderr)
        sys.exit(1)
    for key in ("total_servers", "scored_count", "unscored_count", "unscored_sample"):
        if key not in data:
            print(f"FAIL: missing key '{key}' in response", file=sys.stderr)
            sys.exit(1)

    print("PASS")
    sys.exit(0)
