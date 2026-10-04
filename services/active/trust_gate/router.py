# deps: fastapi, pydantic, sqlalchemy
"""Trust Gate Service.

Provides trust gate evaluation for MCP servers using the trust_gating_override
logic. Evaluates a server's trust posture based on its registry entry and
LLM axis scores, returning the calibrated verdict and masquerade detection.

GET /api/trust_gate/evaluate/{server_id}
    Evaluate trust gate for a server by server_id.
    Reads registry entry + axis scores from app DB; applies trust_gating_override.

GET /api/trust_gate/evaluate?url=...
    Evaluate trust gate for an arbitrary URL + optional axis labels inline.
    Pure computation -- no DB required.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry / mcp_llm_axis_scores.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate as _trust_gate

router = APIRouter(prefix="/api/trust_gate", tags=["trust_gate"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class TrustGateEvidence(BaseModel):
    """Breakdown of trust gate decision factors."""
    github_org: Optional[str] = Field(None, description="GitHub org extracted from URL")
    host: Optional[str] = Field(None, description="Host extracted from URL")
    host_verified: bool = Field(False, description="True if host matches a verified publisher host")
    maintainer_trust_label: Optional[str] = Field(None, description="Model's maintainer_trust axis label")
    original_overall_risk: Optional[str] = Field(None, description="Model's original overall_risk label")
    masquerade_detected: bool = Field(False, description="True if homoglyph/typosquat suspected")


class TrustGateEvaluation(BaseModel):
    """Result of applying the trust gate to a server."""
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    evidence: TrustGateEvidence
    trusted: bool = Field(False, description="True if server receives trust pass")
    trust_basis: Optional[str] = Field(
        None,
        description="Basis for trust: verified_publisher:github_org, "
                    "verified_publisher:host, model_maintainer_established, "
                    "or model_maintainer_verified",
    )
    original_risk: Optional[str] = Field(None, description="Original model overall_risk label")
    published_risk: Optional[str] = Field(
        None,
        description="Published overall_risk after trust gate calibration; "
                    "capped at MEDIUM for trusted publishers",
    )
    masquerade_flag: bool = Field(
        False,
        description="True if URL/organization appears to impersonate a well-known brand",
    )
    display_label: str = Field(
        "Automated heuristic assessment",
        description="Human-readable label for this server's trust verdict",
    )


class InlineEvaluationRequest(BaseModel):
    url: str = Field(..., description="MCP server URL")
    name: Optional[str] = Field(None, description="Server name (optional)")
    axis_labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional axis labels to evaluate, e.g. {'overall_risk': 'HIGH', 'maintainer_trust': 'ESTABLISHED'}",
    )


class InlineEvaluationResponse(BaseModel):
    url: str
    name: Optional[str] = None
    trusted: bool
    trust_basis: Optional[str] = None
    original_risk: Optional[str] = None
    published_risk: Optional[str] = None
    masquerade_flag: bool
    display_label: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_axis_labels(db: Session, server_id: str) -> Dict[str, str]:
    """Fetch the most-recent label for each axis for a given server."""
    rows = db.execute(
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .where(McpLlmAxisScore.server_id == server_id)
        .distinct(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
    ).all()
    return {axis: label for axis, label in rows if label}


def _build_evaluation(
    server_id: str,
    name: Optional[str],
    url: Optional[str],
    registry_source: Optional[str],
    axis_labels: Dict[str, str],
) -> TrustGateEvaluation:
    """Apply trust_gate logic and assemble a TrustGateEvaluation response."""
    from trust_gating_override import github_org as _github_org, host_of as _host_of
    from trust_gating_override import _host_verified as _hv

    org = _github_org(url)
    host = _host_of(url)
    host_verified = _hv(host)

    result = _trust_gate(url, name, axis_labels)

    evidence = TrustGateEvidence(
        github_org=org,
        host=host,
        host_verified=host_verified,
        maintainer_trust_label=axis_labels.get("maintainer_trust"),
        original_overall_risk=axis_labels.get("overall_risk"),
        masquerade_detected=result.get("masquerade_flag", False),
    )

    return TrustGateEvaluation(
        server_id=server_id,
        name=name,
        url=url,
        registry_source=registry_source,
        evidence=evidence,
        trusted=result.get("trusted", False),
        trust_basis=result.get("trust_basis"),
        original_risk=result.get("original_overall_risk"),
        published_risk=result.get("published_overall_risk"),
        masquerade_flag=result.get("masquerade_flag", False),
        display_label=result.get("display_label", "Automated heuristic assessment"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/evaluate/{server_id}",
    response_model=TrustGateEvaluation,
    summary="Evaluate trust gate for a server by server_id",
)
def evaluate_by_server_id(
    server_id: str,
    db: Session = Depends(get_session),
) -> TrustGateEvaluation:
    """
    Look up a server by server_id and apply the trust gate evaluation.

    Reads the registry entry (name, url, source) and the most-recent axis
    scores (overall_risk, maintainer_trust, etc.) from the app DB, then
    returns the calibrated trust verdict.
    """
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_id!r}")

    axis_labels = _latest_axis_labels(db, server_id)

    return _build_evaluation(
        server_id=server_id,
        name=reg.name,
        url=reg.url,
        registry_source=reg.registry_source,
        axis_labels=axis_labels,
    )


@router.get(
    "/evaluate",
    response_model=InlineEvaluationResponse,
    summary="Evaluate trust gate for an arbitrary URL and axis labels",
)
def evaluate_inline(
    url: str = Query(..., description="MCP server URL to evaluate"),
    name: Optional[str] = Query(None, description="Server name"),
    overall_risk: Optional[str] = Query(
        None,
        alias="overall_risk",
        description="Model overall_risk label, e.g. HIGH, CRITICAL",
    ),
    maintainer_trust: Optional[str] = Query(
        None,
        alias="maintainer_trust",
        description="Model maintainer_trust label, e.g. ESTABLISHED, VERIFIED",
    ),
) -> InlineEvaluationResponse:
    """
    Evaluate trust gate for an arbitrary URL with optional axis labels.
    No database lookup is required -- this is a pure computation endpoint.
    """
    axis_labels: Dict[str, str] = {}
    if overall_risk:
        axis_labels["overall_risk"] = overall_risk
    if maintainer_trust:
        axis_labels["maintainer_trust"] = maintainer_trust

    result = _trust_gate(url, name, axis_labels)

    return InlineEvaluationResponse(
        url=url,
        name=name,
        trusted=result.get("trusted", False),
        trust_basis=result.get("trust_basis"),
        original_risk=result.get("original_overall_risk"),
        published_risk=result.get("published_overall_risk"),
        masquerade_flag=result.get("masquerade_flag", False),
        display_label=result.get("display_label", "Automated heuristic assessment"),
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed test data
    sess = TestSession()
    sess.add(McpServerRegistry(
        server_id="srv-stripe",
        name="Stripe MCP",
        url="https://github.com/stripe/agent-toolkit",
        registry_source="official",
    ))
    sess.add(McpServerRegistry(
        server_id="srv-nomock",
        name="g00gle-mcp",
        url="https://github.com/g00gle/mcp",
        registry_source="community",
    ))
    for i, (ax, lbl) in enumerate([
        ("overall_risk", "HIGH"),
        ("maintainer_trust", "ESTABLISHED"),
        ("auth_strength", "STRONG"),
    ], start=1):
        sess.add(McpLlmAxisScore(
            id=i,
            server_id="srv-stripe",
            axis_name=ax,
            label=lbl,
            model_version="v3.0_test",
            scored_at=datetime.now(timezone.utc),
        ))
    for i, (ax, lbl) in enumerate([
        ("overall_risk", "HIGH"),
        ("maintainer_trust", "UNKNOWN"),
    ], start=100):
        sess.add(McpLlmAxisScore(
            id=i,
            server_id="srv-nomock",
            axis_name=ax,
            label=lbl,
            model_version="v3.0_test",
            scored_at=datetime.now(timezone.utc),
        ))
    sess.commit()
    sess.close()

    app = FastAPI()
    app.include_router(router)

    def _override():
        d = TestSession()
        try:
            yield d
        finally:
            d.close()

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test 1: verified publisher (stripe) -> trusted, capped MEDIUM
    r = client.get("/api/trust_gate/evaluate/srv-stripe")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv-stripe"
    assert j["name"] == "Stripe MCP"
    assert j["trusted"] is True, f"Stripe should be trusted: {j}"
    assert j["trust_basis"] == "verified_publisher:github_org", j
    assert j["original_risk"] == "HIGH", j
    assert j["published_risk"] == "MEDIUM", f"Trusted publisher should be capped: {j}"
    assert j["masquerade_flag"] is False, j
    assert j["evidence"]["github_org"] == "stripe", j

    # Test 2: masquerade candidate (g00gle) -> masquerade detected, not trusted
    r = client.get("/api/trust_gate/evaluate/srv-nomock")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv-nomock"
    assert j["masquerade_flag"] is True, f"g00gle should be flagged as masquerade: {j}"
    assert j["trusted"] is False, j
    assert j["evidence"]["masquerade_detected"] is True, j

    # Test 3: 404 for unknown server
    r = client.get("/api/trust_gate/evaluate/nonexistent-server")
    assert r.status_code == 404, r.text

    # Test 4: inline evaluation -- verified host
    r = client.get(
        "/api/trust_gate/evaluate",
        params={"url": "https://github.com/microsoft/vscode-mcp", "overall_risk": "CRITICAL", "maintainer_trust": "ESTABLISHED"},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["trusted"] is True, f"microsoft org should be trusted: {j}"
    assert j["published_risk"] == "MEDIUM", j
    assert j["masquerade_flag"] is False, j

    # Test 5: inline evaluation -- masquerade URL
    r = client.get(
        "/api/trust_gate/evaluate",
        params={"url": "https://github.com/g00gle-official/mcp", "overall_risk": "HIGH"},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["masquerade_flag"] is True, f"g00gle should be masquerade: {j}"
    assert j["trusted"] is False, j

    # Test 6: inline evaluation -- no trust pass
    r = client.get(
        "/api/trust_gate/evaluate",
        params={"url": "https://example.com/unknown-repo", "overall_risk": "LOW"},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["trusted"] is False, j
    assert j["published_risk"] == "LOW", j
    assert j["masquerade_flag"] is False, j

    print("PASS")
