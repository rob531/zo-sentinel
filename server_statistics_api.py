"""server_statistics_api.py -- Aggregate server statistics endpoint.

Returns aggregate metrics from mcp_server_registry and mcp_llm_axis_scores tables.
Reads: risk_tier, verdict, registry_source, trust_score, scored_at.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["statistics"])

# ===================== Minimal auth (mirrors exemplar structure) =====================
import base64
import json
import os
import time
import urllib.request

import jwt
from jwt import PyJWKClient

_CLERK_PK = os.getenv("CLERK_PUBLISHABLE_KEY", "")
_CLERK_SK = os.getenv("CLERK_SECRET_KEY", "")


def _clerk_host() -> str:
    try:
        return base64.b64decode(_CLERK_PK.split("_")[2] + "===").decode().rstrip("$")
    except Exception:
        return ""


_CLERK_ISS = f"https://{_clerk_host()}" if _clerk_host() else ""
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks() -> Optional[PyJWKClient]:
    global _jwks_client
    if _jwks_client is None and _CLERK_ISS:
        _jwks_client = PyJWKClient(_CLERK_ISS + "/.well-known/jwks.json", timeout=8, lifespan=3600)
    return _jwks_client


try:
    if _CLERK_ISS:
        _get_jwks().get_signing_keys()
except Exception:
    pass


class Principal(BaseModel):
    user_id: str
    role: str = "public"


_bearer = HTTPBearer(auto_error=False)


def get_principal(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Principal:
    if creds is None:
        raise Principal(user_id="anon", role="public")
    if not _CLERK_ISS:
        return Principal(user_id=creds.credentials[:20], role="public")
    try:
        signing = _get_jwks().get_signing_key_from_jwt(creds.credentials)
        claims = jwt.decode(creds.credentials, signing.key, algorithms=["RS256"],
                            issuer=_CLERK_ISS, leeway=10, options={"verify_aud": False})
        sub = claims.get("sub", "anon")
        rc = claims.get("role") or ""
        if isinstance(claims.get("public_metadata"), dict):
            rc = claims["public_metadata"].get("role") or rc
        rc = (rc or "").strip().lower()
        role = rc if rc in ("admin", "insider", "public") else "public"
        return Principal(user_id=sub, role=role)
    except Exception:
        return Principal(user_id="anon", role="public")


class ServerStatistics(BaseModel):
    total_servers: int
    by_risk_tier: Dict[str, int]
    by_verdict: Dict[str, int]
    by_registry_source: Dict[str, int]
    avg_trust_score: Optional[float] = None
    recent_ingestion_count_24h: int


@router.get("/servers/statistics", response_model=ServerStatistics)
def get_server_statistics(
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ServerStatistics:
    """Aggregate statistics across all servers in the registry."""
    # total_servers
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0

    # by_risk_tier
    risk_tier_rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    by_risk_tier = {str(r or "unknown"): c for r, c in risk_tier_rows}

    # by_verdict
    verdict_rows = db.execute(
        select(McpServerRegistry.verdict, func.count())
        .group_by(McpServerRegistry.verdict)
    ).all()
    by_verdict = {str(v or "none"): c for v, c in verdict_rows}

    # by_registry_source
    source_rows = db.execute(
        select(McpServerRegistry.registry_source, func.count())
        .group_by(McpServerRegistry.registry_source)
    ).all()
    by_registry_source = {str(s or "unknown"): c for s, c in source_rows}

    # avg_trust_score
    avg_score = db.execute(
        select(func.avg(McpServerRegistry.trust_score))
    ).scalar()

    # recent_ingestion_count_24h (from mcp_llm_axis_scores scored_at)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = db.execute(
        select(func.count(McpLlmAxisScore.id.distinct()))
        .where(McpLlmAxisScore.scored_at >= cutoff)
    ).scalar() or 0

    return ServerStatistics(
        total_servers=total,
        by_risk_tier=by_risk_tier,
        by_verdict=by_verdict,
        by_registry_source=by_registry_source,
        avg_trust_score=float(avg_score) if avg_score is not None else None,
        recent_ingestion_count_24h=recent,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed test data: two servers with different risk tiers, verdicts, sources
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Alpha", url="https://example.com/alpha",
                            registry_source="github", risk_tier="LOW", verdict="approved",
                            trust_score=0.85))
    s.add(McpServerRegistry(server_id="srv2", name="Beta", url="https://example.com/beta",
                            registry_source="npm", risk_tier="HIGH", verdict="flagged",
                            trust_score=0.30))
    s.add(McpServerRegistry(server_id="srv3", name="Gamma", url="https://example.com/gamma",
                            registry_source="github", risk_tier="MEDIUM", verdict="approved",
                            trust_score=0.55))
    # Seed axis scores with scored_at
    now = datetime.now(timezone.utc)
    for i, (sid, ax, lbl) in enumerate([
        ("srv1", "overall_risk", "LOW"), ("srv2", "overall_risk", "HIGH"),
        ("srv3", "overall_risk", "MEDIUM")], start=1):
        s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label=lbl,
                              model_version="v3.0_40974559", scored_at=now))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")
    c = TestClient(app)

    r = c.get("/api/servers/statistics")
    assert r.status_code == 200, r.text
    j = r.json()

    # Assertions per acceptance criteria
    assert j["total_servers"] == 3, f"expected 3, got {j['total_servers']}"
    assert "by_risk_tier" in j, "missing by_risk_tier"
    assert "by_verdict" in j, "missing by_verdict"
    assert "by_registry_source" in j, "missing by_registry_source"
    assert len(j["by_risk_tier"]) > 0, "by_risk_tier should not be empty"
    assert len(j["by_verdict"]) > 0, "by_verdict should not be empty"
    assert len(j["by_registry_source"]) > 0, "by_registry_source should not be empty"

    # Edge case: 24h count when no recent scores
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    s = TS()
    s.add(McpServerRegistry(server_id="srv4", name="Delta", url="https://example.com/delta",
                            registry_source="pypi", risk_tier="LOW", verdict="approved",
                            trust_score=0.70))
    # scored_at 48h ago -> not counted in 24h window
    old = cutoff - timedelta(hours=24)
    s.add(McpLlmAxisScore(id=99, server_id="srv4", axis_name="overall_risk", label="LOW",
                          model_version="v3.0_40974559", scored_at=old))
    s.commit(); s.close()

    r2 = c.get("/api/servers/statistics")
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["total_servers"] == 4, f"expected 4, got {j2['total_servers']}"
    # recent count should still be 3 (srv4's old score not counted)
    assert j2["recent_ingestion_count_24h"] == 3, f"expected 3 recent, got {j2['recent_ingestion_count_24h']}"

    print("PASS")