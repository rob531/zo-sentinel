# deps: fastapi, pydantic, sqlalchemy
"""server_verdict_detail — detailed per-axis verdict for a single MCP server.

GET /api/servers/{server_id}/verdict
    Returns server metadata, 7-axis scores, overall risk tier, and trust-gate
    override applied.  A CRITICAL axis (escalated=True) forces the overall tier
    to "CRITICAL" regardless of the stored tier.

Auth : public (PRODUCT_SPEC §9 scope).
Data : app Postgres via get_session + McpServerRegistry + McpLlmAxisScore.
Multi-tenancy: server_id is not org-scoped in the schema; trust gating is
  applied via trust_gating_override.trust_gate() to prevent false HIGH/CRITICAL
  verdicts on official publishers.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root is on path so `from app.db` resolves correctly
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_verdict_detail"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class AxisDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    model_version: Optional[str]
    scored_at: Optional[datetime]


class TrustGateInfo(BaseModel):
    trusted: bool
    trust_basis: Optional[str]
    capped: bool
    masquerade_flag: bool
    display_label: Optional[str]


class OverallDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    risk_tier: str
    criteria_version: Optional[str]


class ServerVerdictDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    verdict: Optional[str]
    confidence: Optional[float]
    last_assessed: Optional[datetime]
    axes: dict[str, AxisDetail]
    overall: OverallDetail
    trust_gate: Optional[TrustGateInfo]


# --------------------------------------------------------------------------- #
# Helper: compute trust gate
# --------------------------------------------------------------------------- #
def _build_trust_gate(
    server_id: str,
    server_url: Optional[str],
    server_name: Optional[str],
    axes: dict[str, McpLlmAxisScore],
) -> Optional[TrustGateInfo]:
    """Apply trust gating via trust_gating_override.trust_gate()."""
    try:
        from trust_gating_override import trust_gate as _tg
    except ImportError:
        return None

    axis_labels = {name: (row.label or "") for name, row in axes.items()}
    result = _tg(server_url, server_name, axis_labels)

    return TrustGateInfo(
        trusted=result.get("trusted", False),
        trust_basis=result.get("trust_basis"),
        capped=result.get("capped", False),
        masquerade_flag=result.get("masquerade_flag", False),
        display_label=result.get("display_label"),
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/{server_id}/verdict",
    response_model=ServerVerdictDetail,
    name="server_verdict_detail:get",
    responses={
        404: {"description": "Server or axis scores not found"},
        422: {"description": "Validation error"},
    },
)
def get_server_verdict_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVerdictDetail:
    """Return detailed verdict for a server: metadata + 7-axis scores + trust gate."""
    # Fetch server
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    # Fetch axis scores (newest model_version first)
    axis_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.model_version.desc())
        .all()
    )
    if not axis_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No axis scores found for server {server_id}",
        )

    # Build per-axis dict
    axes: dict[str, AxisDetail] = {}
    axis_map: dict[str, McpLlmAxisScore] = {}
    for row in axis_rows:
        axes[row.axis_name] = AxisDetail.model_validate(row)
        axis_map[row.axis_name] = row

    # Overall tier: any escalated axis → CRITICAL
    forced_tier = (
        "CRITICAL"
        if any(bool(a.escalated) for a in axis_rows)
        else (server.risk_tier or "UNKNOWN")
    )
    criteria_version = axis_rows[0].decision_rule_version if axis_rows else None

    # Trust gate
    trust_gate_info = _build_trust_gate(
        server.server_id, server.url, server.name, axis_map
    )

    return ServerVerdictDetail(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        last_assessed=server.last_assessed,
        axes=axes,
        overall=OverallDetail(risk_tier=forced_tier, criteria_version=criteria_version),
        trust_gate=trust_gate_info,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    # --- Seed servers via ORM (server_id is the PK, no BigInteger needed) ---
    with _TS() as _db:
        _db.add(McpServerRegistry(
            server_id="srv-001",
            name="Official Stripe MCP",
            registry_source="github",
            url="https://github.com/stripe/stripe-mcp",
            risk_tier="high",
            verdict="published",
            confidence=0.95,
            last_assessed=datetime(2024, 6, 15, tzinfo=timezone.utc),
        ))
        _db.add(McpServerRegistry(
            server_id="srv-002",
            name="Unknown Server",
            registry_source="npm",
            url="https://example.com/unknown",
            risk_tier="low",
            verdict="clean",
            confidence=0.80,
            last_assessed=datetime(2024, 6, 15, tzinfo=timezone.utc),
        ))
        _db.add(McpServerRegistry(
            server_id="srv-no-scores",
            name="No Scores Server",
            risk_tier="unknown",
        ))
        _db.commit()

    # --- Seed axis scores via raw SQL (avoids SQLite BigInteger autoincrement issue) ---
    _AXES = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]
    # srv-001: no escalated axes; maintainer_trust ESTABLISHED → trust gate applies
    # stored risk_tier is "high", but no escalated → overall stays "high"
    _AXIS_DATA_001 = [
        (0.10, 0.05, 0.10, 0, "LOW",         "v1", "v1"),
        (0.05, 0.02, 0.05, 0, "LOW",          "v1", "v1"),
        (0.20, 0.08, 0.15, 0, "LOW",           "v1", "v1"),
        (0.15, 0.06, 0.12, 0, "LOW",           "v1", "v1"),
        (0.08, 0.03, 0.07, 0, "LOW",           "v1", "v1"),
        (0.90, 0.02, 0.05, 0, "ESTABLISHED",  "v1", "v1"),
        (0.12, 0.05, 0.10, 0, "LOW",           "v1", "v1"),
    ]
    # srv-002: overall_risk escalated → forces CRITICAL regardless of stored "low"
    _AXIS_DATA_002 = [
        (0.70, 0.80, 0.50, 1, "HIGH",     "v1", "v1"),
        (0.05, 0.02, 0.05, 0, "LOW",      "v1", "v1"),
        (0.20, 0.08, 0.15, 0, "LOW",      "v1", "v1"),
        (0.15, 0.06, 0.12, 0, "LOW",      "v1", "v1"),
        (0.08, 0.03, 0.07, 0, "LOW",      "v1", "v1"),
        (0.10, 0.04, 0.08, 0, "LOW",      "v1", "v1"),
        (0.12, 0.05, 0.10, 0, "LOW",      "v1", "v1"),
    ]

    _idx = 1
    with _TS() as _db:
        for _sid, _data in [("srv-001", _AXIS_DATA_001), ("srv-002", _AXIS_DATA_002)]:
            for _ax_idx, _ax_name in enumerate(_AXES):
                _p_top, _p_crit, _p_dang, _esc_int, _lbl, _drv, _mv = _data[_ax_idx]
                _db.execute(
                    text("""
                        INSERT INTO mcp_llm_axis_scores
                            (id, server_id, axis_name, label, label_index,
                             p_top, p_critical, p_danger, escalated,
                             model_version, decision_rule_version, probs,
                             escalated_to, adapter_sha256, scored_at)
                        VALUES (:id, :sid, :aname, :lbl, :lidx,
                                :ptop, :pcrit, :pdang, :esc,
                                :mv, :drv, '{}', :eto, 'sha256_test', :scored)
                    """),
                    {
                        "id": _idx, "sid": _sid, "aname": _ax_name,
                        "lbl": _lbl, "lidx": _ax_idx,
                        "ptop": _p_top, "pcrit": _p_crit, "pdang": _p_dang,
                        "esc": _esc_int, "mv": _mv, "drv": _drv,
                        "eto": "CRITICAL" if _esc_int else None,
                        "scored": "2024-06-15 00:00:00",
                    },
                )
                _idx += 1
        _db.commit()

    def _override_session():
        _s = _TS()
        try:
            yield _s
        finally:
            _s.close()

    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_session] = _override_session
    _client = TestClient(_app)

    # --- Happy path: srv-001 (trust-gated official publisher) ---
    _r = _client.get("/api/servers/srv-001/verdict")
    assert _r.status_code == 200, (
        f"srv-001: expected 200, got {_r.status_code}: {_r.text}"
    )
    _d = _r.json()
    assert _d["server_id"] == "srv-001"
    assert _d["name"] == "Official Stripe MCP"
    assert len(_d["axes"]) == 7, f"Expected 7 axes, got {len(_d['axes'])}"
    # No escalated axes → overall risk_tier comes from server.risk_tier = "high"
    assert _d["overall"]["risk_tier"] == "high", (
        f"srv-001 no escalated axes → 'high', got {_d['overall']['risk_tier']}"
    )
    assert "trust_gate" in _d, "trust_gate field missing"
    assert _d["trust_gate"] is not None, "trust_gate should not be None"
    assert _d["trust_gate"]["trusted"] is True, (
        "Stripe server should be trusted by gate"
    )

    # --- srv-002: escalated overall_risk forces CRITICAL (overrides stored "low") ---
    _r2 = _client.get("/api/servers/srv-002/verdict")
    assert _r2.status_code == 200, f"srv-002: expected 200, got {_r2.status_code}"
    _d2 = _r2.json()
    assert len(_d2["axes"]) == 7
    assert _d2["overall"]["risk_tier"] == "CRITICAL", (
        f"srv-002 escalated overall_risk → CRITICAL, "
        f"got {_d2['overall']['risk_tier']}"
    )

    # --- 404 for unknown server ---
    _r3 = _client.get("/api/servers/nonexistent/verdict")
    assert _r3.status_code == 404, (
        f"Expected 404 for unknown server, got {_r3.status_code}"
    )

    # --- 404 when server exists but has no axis scores ---
    _r4 = _client.get("/api/servers/srv-no-scores/verdict")
    assert _r4.status_code == 404, (
        f"Expected 404 for server with no scores, got {_r4.status_code}"
    )

    print("PASS")
    _sys.exit(0)
