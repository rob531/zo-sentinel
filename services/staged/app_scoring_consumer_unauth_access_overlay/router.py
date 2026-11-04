"""app_scoring_consumer_unauth_access_overlay.py -- consistency overlay between the
model-assessed auth_strength axis and observed unauthenticated access.

The SFT model scores every MCP server on seven axes (mcp_llm_axis_scores), one of
which is auth_strength. A live prober separately records whether a server actually
serves unauthenticated requests (mcp_unauth_access, isolated from the score table
so a probe row can never become a serving change).

This module joins the two, per server, and answers one question: does the model's
"auth is strong" verdict agree with what the prober observed? A disagreement is an
inconsistency worth surfacing -- the model says strong auth, the evidence says the
server served without authentication.

Pure functions (build_overlay / summarize_overlay) do the reasoning; the FastAPI
router reads the REAL app data layer (app.db / app.models) and feeds those rows in.
No DB writes, no network, no duckdb.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpUnauthAccess

AUTH_AXIS = "auth_strength"
# p_top at or above this means the model is confident the server's auth is strong.
STRONG_AUTH_THRESHOLD = 0.5
# Values that mean "no unauthenticated access observed" (or unknown) -> not evidence.
NEGATIVE_UNAUTH_VALUES = frozenset({
    "", "no", "none", "false", "0", "closed", "gated", "unreachable",
})

router = APIRouter(prefix="/api", tags=["unauth-overlay"])


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
def _is_unauth_observed(value: Any) -> bool:
    """True when the stored exposure label means 'served unauthenticated'."""
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return text not in NEGATIVE_UNAUTH_VALUES


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_overlay(server_id: str, axis_rows: List[dict],
                  unauth_rows: List[dict]) -> dict:
    """Join auth_strength axis rows with unauthenticated-access evidence.

    axis_rows: dicts with axis_name, label, p_top, scored_at (only auth_strength
    is used). unauth_rows: dicts with server_id, url, unauth_access, source,
    updated_at.

    Returns {'server_id', 'auth_p_top', 'unauth_access', 'unauth_urls',
    'inconsistent', 'evidence'}. inconsistent is True only when the model is
    confident auth is strong (p_top >= 0.5) while unauthenticated access was
    observed.
    """
    auth_row: Optional[dict] = None
    for row in axis_rows or []:
        if str(row.get("axis_name") or "") == AUTH_AXIS:
            if auth_row is None:
                auth_row = row
            else:
                # keep the most recent scoring for the axis
                prev = auth_row.get("scored_at")
                cur = row.get("scored_at")
                if prev is None or (cur is not None and str(cur) > str(prev)):
                    auth_row = row

    p_top = _as_float(auth_row.get("p_top")) if auth_row else None

    matching = [r for r in (unauth_rows or [])
                if not r.get("server_id") or str(r.get("server_id")) == str(server_id)]
    if not matching:
        matching = list(unauth_rows or [])

    unauth_access: Optional[str] = None
    unauth_urls: List[str] = []
    unauth_source: Optional[str] = None
    unauth_updated_at: Any = None
    for row in matching:
        if unauth_access is None and row.get("unauth_access") is not None:
            unauth_access = str(row.get("unauth_access"))
            unauth_source = row.get("source")
            unauth_updated_at = row.get("updated_at")
        url = row.get("url")
        if url and str(url) not in unauth_urls:
            unauth_urls.append(str(url))

    observed = _is_unauth_observed(unauth_access)
    inconsistent = bool(p_top is not None and observed and p_top >= STRONG_AUTH_THRESHOLD)

    if not auth_row:
        reason = "no_auth_strength_axis_row"
    elif p_top is None:
        reason = "auth_strength_axis_row_without_p_top"
    elif not observed:
        reason = "no_unauthenticated_access_observed"
    elif not inconsistent:
        reason = "model_does_not_claim_strong_auth"
    else:
        reason = "model_claims_strong_auth_but_unauthenticated_access_observed"

    evidence = {
        "axis_name": AUTH_AXIS,
        "axis_present": auth_row is not None,
        "label": (auth_row or {}).get("label"),
        "p_top": p_top,
        "scored_at": (auth_row or {}).get("scored_at"),
        "unauth_observed": observed,
        "unauth_source": unauth_source,
        "unauth_updated_at": unauth_updated_at,
        "threshold": STRONG_AUTH_THRESHOLD,
        "reason": reason,
    }

    return {
        "server_id": server_id,
        "auth_p_top": p_top,
        "unauth_access": unauth_access,
        "unauth_urls": unauth_urls,
        "inconsistent": inconsistent,
        "evidence": evidence,
    }


def summarize_overlay(rows: List[dict]) -> dict:
    """Aggregate overlay rows into {'servers', 'inconsistent', 'ratio',
    'by_unauth_access'}."""
    rows = list(rows or [])
    by_unauth: Dict[str, int] = {}
    inconsistent = 0
    for row in rows:
        value = row.get("unauth_access")
        key = str(value) if value is not None else "unknown"
        by_unauth[key] = by_unauth.get(key, 0) + 1
        if row.get("inconsistent"):
            inconsistent += 1
    servers = len(rows)
    ratio = (inconsistent / servers) if servers else 0.0
    return {
        "servers": servers,
        "inconsistent": inconsistent,
        "ratio": ratio,
        "by_unauth_access": by_unauth,
    }


# --------------------------------------------------------------------------- #
# Real data layer (app.db / app.models)
# --------------------------------------------------------------------------- #
class OverlayResponse(BaseModel):
    server_id: str
    auth_p_top: Optional[float] = None
    unauth_access: Optional[str] = None
    unauth_urls: List[str] = []
    inconsistent: bool = False
    evidence: Dict[str, Any] = {}


class OverlaySummaryResponse(BaseModel):
    servers: int
    inconsistent: int
    ratio: float
    by_unauth_access: Dict[str, int]


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def load_axis_rows(db: Session, server_id: str) -> List[dict]:
    """auth_strength axis rows for a server (latest model version when known)."""
    mv = _latest_model_version(db, server_id)
    stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == AUTH_AXIS,
    )
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    rows = db.execute(stmt).scalars().all()
    return [{"axis_name": r.axis_name, "label": r.label, "p_top": r.p_top,
             "scored_at": r.scored_at} for r in rows]


def load_unauth_rows(db: Session, server_id: str) -> List[dict]:
    """Unauthenticated-access evidence rows for a server."""
    rows = db.execute(
        select(McpUnauthAccess).where(McpUnauthAccess.server_id == server_id)
    ).scalars().all()
    return [{"server_id": r.server_id, "url": r.url,
             "unauth_access": r.unauth_access, "source": r.source,
             "updated_at": r.updated_at} for r in rows]


def overlay_for_server(db: Session, server_id: str) -> dict:
    return build_overlay(server_id, load_axis_rows(db, server_id),
                         load_unauth_rows(db, server_id))


@router.get("/unauth-overlay/summary", response_model=OverlaySummaryResponse)
def get_unauth_overlay_summary(limit: int = 200,
                               db: Session = Depends(get_session)) -> OverlaySummaryResponse:
    """Summary of the consistency overlay across servers with probe evidence."""
    server_ids = [r[0] for r in db.execute(
        select(McpUnauthAccess.server_id).limit(max(1, min(int(limit), 1000)))
    ).all()]
    rows = [overlay_for_server(db, sid) for sid in server_ids]
    return OverlaySummaryResponse(**summarize_overlay(rows))


@router.get("/unauth-overlay/{server_id}", response_model=OverlayResponse)
def get_unauth_overlay(server_id: str, db: Session = Depends(get_session)) -> OverlayResponse:
    """Per-server auth_strength vs observed unauthenticated-access consistency."""
    return OverlayResponse(**overlay_for_server(db, server_id))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- pure-function contract ---
    strong = build_overlay(
        "srv-strong",
        [{"axis_name": "auth_strength", "label": "STRONG", "p_top": 0.9}],
        [{"server_id": "srv-strong", "url": "https://x.example/mcp",
          "unauth_access": "yes", "source": "probe", "updated_at": None}],
    )
    assert strong["inconsistent"] is True, strong
    assert strong["auth_p_top"] == 0.9, strong
    assert strong["unauth_urls"] == ["https://x.example/mcp"], strong

    weak = build_overlay(
        "srv-weak",
        [{"axis_name": "auth_strength", "label": "WEAK", "p_top": 0.1}],
        [{"server_id": "srv-weak", "url": "https://y.example/mcp",
          "unauth_access": "yes", "source": "probe", "updated_at": None}],
    )
    assert weak["inconsistent"] is False, weak

    missing = build_overlay("srv-none", [], [])
    assert missing["auth_p_top"] is None, missing
    assert missing["inconsistent"] is False, missing

    synthetic = [strong, weak, missing]
    summary = summarize_overlay(synthetic)
    assert summary["servers"] == 3, summary
    assert summary["inconsistent"] == 1, summary
    assert abs(summary["ratio"] - 1 / 3) < 1e-9, summary
    assert summary["by_unauth_access"]["yes"] == 2, summary

    # --- data-wired path: real models over an overridden session ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for _i, (ax, lbl, ptop) in enumerate((
            ("overall_risk", "HIGH", 0.7), ("auth_strength", "STRONG", 0.9)), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              p_top=ptop, model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="auth_strength",
                          label="WEAK", p_top=0.1, model_version="v3.0_40974559"))
    s.add(McpUnauthAccess(server_id="srv1", url="https://x.example/mcp",
                          unauth_access="OPEN", source="probe"))
    s.add(McpUnauthAccess(server_id="srv2", url="https://y.example/mcp",
                          unauth_access="GATED", source="probe"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    r = c.get("/api/unauth-overlay/srv1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["inconsistent"] is True, j
    assert j["auth_p_top"] == 0.9, j
    assert j["unauth_access"] == "OPEN", j

    r2 = c.get("/api/unauth-overlay/srv2")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["inconsistent"] is False, j2
    assert j2["auth_p_top"] == 0.1, j2

    r3 = c.get("/api/unauth-overlay/summary")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["servers"] == 2, j3
    assert j3["inconsistent"] == 1, j3

    print("PASS")
