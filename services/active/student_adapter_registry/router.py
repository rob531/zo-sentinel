# deps: fastapi, sqlalchemy, pydantic
"""student_adapter_registry -- registry of active SFT student model adapters.

GET /api/scoring/adapters
  Returns all unique (model_version, adapter_sha256) pairs found in
  mcp_llm_axis_scores, enriched with axes_covered (list of axis_name
  strings), server_count, and latest_scored_at.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + McpLlmAxisScore.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["student_adapter_registry"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class AdapterInfo(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: Optional[str]
    axes_covered: List[str]
    server_count: int
    latest_scored_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AdapterRegistryResponse(BaseModel):
    adapters: List[AdapterInfo]


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get("/scoring/adapters", response_model=AdapterRegistryResponse)
def list_adapters(
    db: Session = Depends(get_session),
) -> AdapterRegistryResponse:
    """
    Build and return the student-adapter registry keyed by
    (model_version, adapter_sha256), enriched with per-axis labels.
    """
    # Core aggregation: group by adapter key, capture decision_rule_version
    # and latest_scored_at.
    base_q = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.max(McpLlmAxisScore.decision_rule_version).label("decision_rule_version"),
            func.count(distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
        )
        .order_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )
    base_rows = db.execute(base_q).all()

    if not base_rows:
        return AdapterRegistryResponse(adapters=[])

    # Per-adapter distinct axes list.
    axes_q = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.list_agg(distinct(McpLlmAxisScore.axis_name), ",")
            .label("axes_list"),
        )
        .group_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )
    axes_map = {
        (r.model_version, r.adapter_sha256): r.axes_list.split(",")
        if r.axes_list
        else []
        for r in db.execute(axes_q).all()
    }

    adapters = [
        AdapterInfo(
            model_version=r.model_version,
            adapter_sha256=r.adapter_sha256,
            decision_rule_version=r.decision_rule_version,
            axes_covered=axes_map.get((r.model_version, r.adapter_sha256), []),
            server_count=r.server_count,
            latest_scored_at=r.latest_scored_at,
        )
        for r in base_rows
    ]

    return AdapterRegistryResponse(adapters=adapters)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory SQLite seeded with the minimal mcp_llm_axis_scores schema.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(64) NOT NULL,
                axis_name VARCHAR(64) NOT NULL,
                label VARCHAR(32) NOT NULL,
                label_index INTEGER NOT NULL,
                probs TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER NOT NULL DEFAULT 0,
                escalated_to VARCHAR(32),
                decision_rule_version VARCHAR(32),
                model_version VARCHAR(64) NOT NULL,
                adapter_sha256 VARCHAR(64) NOT NULL,
                scored_at TIMESTAMP NOT NULL
            )
        """))
        conn.commit()

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestingSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # Seed data: two adapters, student-v1 covers 2 axes across 1 server,
    # student-v2 covers 1 axis across 1 server.
    now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    with TestingSession() as sess:
        rows = [
            McpLlmAxisScore(
                id=1,
                model_version="student-v1",
                adapter_sha256="sha111",
                decision_rule_version="rule-v1",
                axis_name="security",
                label="high",
                label_index=2,
                server_id="srv-1",
                scored_at=now,
                probs="[0.1,0.2,0.7]",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.7,
                escalated=False,
                escalated_to=None,
            ),
            McpLlmAxisScore(
                id=2,
                model_version="student-v1",
                adapter_sha256="sha111",
                decision_rule_version="rule-v1",
                axis_name="compliance",
                label="medium",
                label_index=1,
                server_id="srv-1",
                scored_at=now,
                probs="[0.2,0.6,0.2]",
                p_critical=0.2,
                p_danger=0.6,
                p_top=0.6,
                escalated=False,
                escalated_to=None,
            ),
            McpLlmAxisScore(
                id=3,
                model_version="student-v2",
                adapter_sha256="sha222",
                decision_rule_version="rule-v2",
                axis_name="security",
                label="low",
                label_index=0,
                server_id="srv-2",
                scored_at=now,
                probs="[0.7,0.2,0.1]",
                p_critical=0.7,
                p_danger=0.2,
                p_top=0.1,
                escalated=True,
                escalated_to="human-review",
            ),
        ]
        for row in rows:
            sess.add(row)
        sess.commit()

    client = TestClient(app)

    # 1. Happy path: two adapters returned.
    r = client.get("/api/scoring/adapters")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    adapters = data.get("adapters", [])
    assert len(adapters) == 2, f"Expected 2 adapters, got {len(adapters)}"

    # 2. student-v1 has 2 axes.
    v1_map = next(
        (a for a in adapters if a["model_version"] == "student-v1"), None
    )
    assert v1_map is not None, "student-v1 adapter not found"
    assert len(v1_map["axes_covered"]) == 2, (
        f"Expected axes_covered=2, got {v1_map['axes_covered']}"
    )
    assert "security" in v1_map["axes_covered"]
    assert "compliance" in v1_map["axes_covered"]
    assert v1_map["server_count"] == 1
    assert v1_map["decision_rule_version"] == "rule-v1"

    # 3. student-v2 has 1 axis.
    v2_map = next(
        (a for a in adapters if a["model_version"] == "student-v2"), None
    )
    assert v2_map is not None, "student-v2 adapter not found"
    assert len(v2_map["axes_covered"]) == 1, (
        f"Expected axes_covered=1, got {v2_map['axes_covered']}"
    )
    assert v2_map["server_count"] == 1
    assert v2_map["decision_rule_version"] == "rule-v2"

    print("PASS")
