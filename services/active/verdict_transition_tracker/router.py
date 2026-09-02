# deps: fastapi, pydantic, sqlalchemy
"""Verdict Transition Tracker API.

GET /api/verdict-transitions
  Returns all servers that changed risk tier over time, derived from axis scoring
  events. Tier transitions are detected by comparing the effective tier per
  scoring event (CRITICAL -> HIGH_RISK_ISOLATED, HIGH -> ELEVATED, etc.).

GET /api/verdict-transitions/{server_id}
  Returns tier transitions for a specific server.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores and
      mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict_transition_tracker"])


# --------------------------------------------------------------------------- #
# Tier derivation (trust_gating_override pattern)
# --------------------------------------------------------------------------- #

KNOWN_AXES = frozenset({
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
})


def derive_effective_tier(label: Optional[str]) -> str:
    """Map an axis label to a risk tier string."""
    if label is None:
        return "UNKNOWN"
    lbl = label.upper()
    if lbl == "CRITICAL":
        return "HIGH_RISK_ISOLATED"
    if lbl == "HIGH":
        return "ELEVATED"
    if lbl == "MEDIUM":
        return "MODERATE"
    if lbl == "LOW":
        return "LOW"
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class TransitionEntry(BaseModel):
    server_id: str
    name: Optional[str]
    from_tier: str
    to_tier: str
    changed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VerdictTransitionsResponse(BaseModel):
    total: int
    transitions: list[TransitionEntry]


class ServerVerdictTransitionsResponse(BaseModel):
    server_id: str
    name: Optional[str]
    total: int
    transitions: list[TransitionEntry]


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

def get_all_transitions(db: Session) -> list[TransitionEntry]:
    """
    Detect tier transitions for all servers by walking axis-score history
    ordered by scored_at.  Each server starts at UNKNOWN; when the effective
    tier derived from the current axis label differs from the previous
    effective tier, a transition record is emitted.
    """
    # Fetch all scores ordered by server_id then scored_at
    rows = (
        db.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.label,
            McpLlmAxisScore.scored_at,
        )
        .order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
        )
        .all()
    )

    if not rows:
        return []

    # Build a name lookup from registry
    name_map: dict[str, Optional[str]] = {}
    server_ids = list({r.server_id for r in rows})
    servers = (
        db.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    for srv in servers:
        name_map[srv.server_id] = srv.name

    transitions: list[TransitionEntry] = []
    prev_tier: Optional[str] = None
    prev_server_id: Optional[str] = None

    for row in rows:
        current_tier = derive_effective_tier(row.label)

        if row.server_id != prev_server_id:
            # New server -- reset
            prev_tier = None
        else:
            if prev_tier is not None and prev_tier != current_tier:
                transitions.append(TransitionEntry(
                    server_id=row.server_id,
                    name=name_map.get(row.server_id),
                    from_tier=prev_tier,
                    to_tier=current_tier,
                    changed_at=row.scored_at,
                ))

        prev_tier = current_tier
        prev_server_id = row.server_id

    return transitions


def get_server_transitions(
    server_id: str,
    db: Session,
) -> tuple[Optional[str], Optional[str], list[TransitionEntry]]:
    """
    Detect tier transitions for one server.  Returns (name, verdict, transitions).
    """
    srv = (
        db.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    name = srv.name if srv else None

    rows = (
        db.query(
            McpLlmAxisScore.label,
            McpLlmAxisScore.scored_at,
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    transitions: list[TransitionEntry] = []
    prev_tier: Optional[str] = None

    for row in rows:
        current_tier = derive_effective_tier(row.label)
        if prev_tier is not None and prev_tier != current_tier:
            transitions.append(TransitionEntry(
                server_id=server_id,
                name=name,
                from_tier=prev_tier,
                to_tier=current_tier,
                changed_at=row.scored_at,
            ))
        prev_tier = current_tier

    return (name, None, transitions)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/verdict-transitions",
    response_model=VerdictTransitionsResponse,
    summary="Get all server verdict tier transitions",
)
def list_verdict_transitions(
    db: Session = Depends(get_session),
) -> VerdictTransitionsResponse:
    """Return all detected tier transitions across all servers."""
    transitions = get_all_transitions(db)
    return VerdictTransitionsResponse(
        total=len(transitions),
        transitions=transitions,
    )


@router.get(
    "/verdict-transitions/{server_id}",
    response_model=ServerVerdictTransitionsResponse,
    summary="Get tier transitions for a specific server",
    responses={404: {"description": "Server not found"}},
)
def get_server_verdict_transitions(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVerdictTransitionsResponse:
    """Return tier transition history for one server."""
    name, _verdict, transitions = get_server_transitions(server_id, db)
    if name is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )
    return ServerVerdictTransitionsResponse(
        server_id=server_id,
        name=name,
        total=len(transitions),
        transitions=transitions,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Build local FastAPI() for self-test (not app.main:app)
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite via StaticPool
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override() -> Generator[Session, None, None]:
        with TestSession() as s:
            yield s

    test_app.dependency_overrides[get_session] = _override

    # Seed data -- use distinct model_version per scoring event to avoid unique
    # constraint violation on (server_id, axis_name, model_version)
    t1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2024, 1, 3, 10, 0, 0, tzinfo=timezone.utc)

    with TestSession() as sess:
        sess.add(McpServerRegistry(server_id="srv-1", name="Alpha", risk_tier="LOW", url="http://a", registry_source="test"))
        sess.add(McpServerRegistry(server_id="srv-2", name="Beta", risk_tier="LOW", url="http://b", registry_source="test"))
        sess.add(McpServerRegistry(server_id="srv-3", name="Gamma", risk_tier="LOW", url="http://c", registry_source="test"))
        sess.add(McpServerRegistry(server_id="srv-4", name="Delta", risk_tier="LOW", url="http://d", registry_source="test"))
        sess.flush()

        # srv-1: LOW -> ELEVATED (HIGH) -> HIGH_RISK_ISOLATED (CRITICAL) = 2 transitions
        sess.add(McpLlmAxisScore(id=1, server_id="srv-1", axis_name="overall_risk", label="LOW", scored_at=t1, model_version="v1", decision_rule_version="r1", probs=None, adapter_sha256="a", escalated=False))
        sess.add(McpLlmAxisScore(id=2, server_id="srv-1", axis_name="overall_risk", label="HIGH", scored_at=t2, model_version="v2", decision_rule_version="r1", probs=None, adapter_sha256="b", escalated=False))
        sess.add(McpLlmAxisScore(id=3, server_id="srv-1", axis_name="overall_risk", label="CRITICAL", scored_at=t3, model_version="v3", decision_rule_version="r1", probs=None, adapter_sha256="c", escalated=True))

        # srv-2: LOW -> ELEVATED (HIGH), stays ELEVATED = 1 transition
        sess.add(McpLlmAxisScore(id=4, server_id="srv-2", axis_name="overall_risk", label="LOW", scored_at=t1, model_version="v1", decision_rule_version="r1", probs=None, adapter_sha256="a", escalated=False))
        sess.add(McpLlmAxisScore(id=5, server_id="srv-2", axis_name="overall_risk", label="HIGH", scored_at=t2, model_version="v2", decision_rule_version="r1", probs=None, adapter_sha256="b", escalated=False))
        sess.add(McpLlmAxisScore(id=6, server_id="srv-2", axis_name="overall_risk", label="HIGH", scored_at=t3, model_version="v3", decision_rule_version="r1", probs=None, adapter_sha256="c", escalated=False))

        # srv-3: LOW -> LOW -> LOW (no transitions)
        sess.add(McpLlmAxisScore(id=7, server_id="srv-3", axis_name="overall_risk", label="LOW", scored_at=t1, model_version="v1", decision_rule_version="r1", probs=None, adapter_sha256="a", escalated=False))
        sess.add(McpLlmAxisScore(id=8, server_id="srv-3", axis_name="overall_risk", label="LOW", scored_at=t2, model_version="v2", decision_rule_version="r1", probs=None, adapter_sha256="b", escalated=False))
        sess.add(McpLlmAxisScore(id=9, server_id="srv-3", axis_name="overall_risk", label="LOW", scored_at=t3, model_version="v3", decision_rule_version="r1", probs=None, adapter_sha256="c", escalated=False))

        # srv-4: MODERATE (MEDIUM) -> MODERATE -> ELEVATED (HIGH) = 1 transition
        sess.add(McpLlmAxisScore(id=10, server_id="srv-4", axis_name="overall_risk", label="MEDIUM", scored_at=t1, model_version="v1", decision_rule_version="r1", probs=None, adapter_sha256="a", escalated=False))
        sess.add(McpLlmAxisScore(id=11, server_id="srv-4", axis_name="overall_risk", label="MEDIUM", scored_at=t2, model_version="v2", decision_rule_version="r1", probs=None, adapter_sha256="b", escalated=False))
        sess.add(McpLlmAxisScore(id=12, server_id="srv-4", axis_name="overall_risk", label="HIGH", scored_at=t3, model_version="v3", decision_rule_version="r1", probs=None, adapter_sha256="c", escalated=False))

        sess.commit()

    client = TestClient(test_app)

    # T1: happy path -- all transitions (srv-1: 2, srv-2: 1, srv-4: 1 = 4)
    resp = client.get("/api/verdict-transitions")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "transitions" in data, "Missing 'transitions' key"
    total = data["total"]
    assert total == 4, f"Expected 4 total transitions, got {total}"
    assert len(data["transitions"]) == 4

    # T2: per-server -- srv-1 has 2 transitions
    resp2 = client.get("/api/verdict-transitions/srv-1")
    assert resp2.status_code == 200, f"srv-1 200, got {resp2.status_code}"
    d2 = resp2.json()
    assert d2["server_id"] == "srv-1"
    assert d2["name"] == "Alpha"
    assert d2["total"] == 2, f"srv-1 expected 2 transitions, got {d2['total']}"

    # T3: srv-2 has 1 transition
    resp3 = client.get("/api/verdict-transitions/srv-2")
    assert resp3.status_code == 200
    d3 = resp3.json()
    assert d3["total"] == 1, f"srv-2 expected 1 transition, got {d3['total']}"

    # T4: srv-3 has 0 transitions
    resp4 = client.get("/api/verdict-transitions/srv-3")
    assert resp4.status_code == 200
    d4 = resp4.json()
    assert d4["total"] == 0, f"srv-3 expected 0 transitions, got {d4['total']}"

    # T5: srv-4 has 1 transition
    resp5 = client.get("/api/verdict-transitions/srv-4")
    assert resp5.status_code == 200
    d5 = resp5.json()
    assert d5["total"] == 1, f"srv-4 expected 1 transition, got {d5['total']}"

    # T6: 404 for unknown server
    resp6 = client.get("/api/verdict-transitions/no-such-server")
    assert resp6.status_code == 404, f"Expected 404, got {resp6.status_code}"

    print("PASS")
