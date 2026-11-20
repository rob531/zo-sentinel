"""config_scan_api.py -- POST /api/scan: "Scan my config" (the acquisition demo).

FATHER's day-3 killer feature (2026-07-02 compressed ruling): paste an mcp.json
(Claude Desktop / mcp client config) and get a per-server risk report -- verdict
tier + the 7 axes + known vulnerabilities, each with provenance -- and an honest
UNKNOWN for servers we haven't scored. Pure demo of the whole moat in one call,
and the strongest single reason for a developer to come back. Reuses the live
registry + verdict + vuln surfaces; introduces no new claim type.

Matching a config entry to a registry server is DETERMINISTIC (vuln_identity
repo/package keys + exact name) -- never fuzzy; an entry we can't identify is
reported as UNKNOWN, not guessed at.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from facet_enum_service import latest_global_model_version
from vuln_exposure_api import server_vulns
from vuln_identity import repo_key, server_identities
from verdict_breakdown_api import Principal, charge_lookup, get_principal

router = APIRouter(prefix="/api", tags=["scan"])
MAX_ENTRIES = 50
_SCAN_CANDIDATE_LIMIT = 250
_URL_RX = re.compile(r"https?://[^\s\"']+")


class ScanRequest(BaseModel):
    config: str            # raw mcp.json text (or any JSON with mcpServers)


def extract_servers(config_text: str) -> List[dict]:
    """Pull candidate servers from an mcp.json. Tolerant of the common shapes:
    {"mcpServers": {"name": {"command":..,"args":[..url..], "url":..}}}.
    Returns [{key, name, hint}] where hint is a url/package string if present."""
    try:
        data = json.loads(config_text)
    except Exception:
        raise HTTPException(status_code=400, detail="config is not valid JSON")
    servers = []
    blocks = data.get("mcpServers") or data.get("servers") or {}
    if isinstance(blocks, dict):
        items = blocks.items()
    elif isinstance(blocks, list):
        items = [(b.get("name", f"server_{i}"), b) for i, b in enumerate(blocks)]
    else:
        items = []
    for name, spec in list(items)[:MAX_ENTRIES]:
        hint = None
        if isinstance(spec, dict):
            hint = spec.get("url")
            if not hint:
                blob = json.dumps(spec)
                m = _URL_RX.search(blob)
                hint = m.group(0) if m else None
            if not hint:
                args = spec.get("args") or []
                for a in args:
                    if isinstance(a, str) and ("/" in a or "@" in a):
                        hint = a
                        break
        servers.append({"name": str(name), "hint": hint})
    return servers


def _candidate_like_tokens(hint: Optional[str], wanted: set[str]) -> List[str]:
    """Build bounded SQL LIKE tokens so Postgres narrows candidates before
    exact `server_identities` intersection, avoiding a full-table 600k-row scan.
    """
    tokens: List[str] = []
    for ident in wanted:
        if ident.startswith("repo:"):
            # ident form: repo:github.com/owner/repo -> match owner/repo in URL or name
            rest = ident[len("repo:"):]
            parts = rest.split("/", 2)
            if len(parts) == 3:
                tokens.append(f"{parts[1]}/{parts[2]}")
        elif ident.startswith("pkg:npm/"):
            pkg = ident[len("pkg:npm/"):]
            if pkg:
                tokens.append(pkg)
                if "/" in pkg:
                    tokens.append(pkg.rsplit("/", 1)[-1])
    if hint:
        h = hint.strip()
        if h.startswith("@") and h not in tokens:
            tokens.append(h)
    deduped: List[str] = []
    seen = set()
    for t in tokens:
        tl = t.lower()
        if tl and len(tl) >= 3 and tl not in seen:
            seen.add(tl)
            deduped.append(t)
    return deduped[:6]


def _resolve_registry_server(db: Session, name: str,
                             hint: Optional[str]) -> Optional[McpServerRegistry]:
    """Deterministic identity resolution: indexed candidate prefilter + exact
    repo/package key intersection, else exact name match. None => UNKNOWN."""
    wanted = set()
    rk = repo_key(hint)
    if rk:
        wanted.add(rk)
    if hint and hint.startswith("@"):        # npm scoped package hint
        wanted.add(f"pkg:npm/{hint.lower()}")
    if wanted:
        tokens = _candidate_like_tokens(hint, wanted)
        if tokens:
            like_clauses = []
            for tok in tokens:
                pat = f"%{tok}%"
                like_clauses.append(McpServerRegistry.url.ilike(pat))
                like_clauses.append(McpServerRegistry.name.ilike(pat))
            candidates = db.execute(
                select(McpServerRegistry)
                .where(or_(*like_clauses))
                .limit(_SCAN_CANDIDATE_LIMIT)
            ).scalars().all()
            for srv in candidates:
                meta = None
                if srv.meta and isinstance(srv.meta, str) and srv.meta.startswith("{"):
                    try:
                        meta = json.loads(srv.meta)
                    except Exception:
                        meta = None
                if server_identities(srv.url, srv.name, meta=meta) & wanted:
                    return srv
                if hint and hint.startswith("@"):
                    h_low = hint.lower()
                    if (srv.name and srv.name.strip().lower() == h_low) or (
                        srv.url and f"/package/{h_low}" in srv.url.lower()
                    ):
                        return srv
    # exact name fallback (case-insensitive)
    row = db.execute(select(McpServerRegistry).where(
        McpServerRegistry.name.ilike(name))).scalars().first()
    return row


def scan_config(db: Session, config_text: str) -> dict:
    entries = extract_servers(config_text)
    mv = latest_global_model_version(db)
    results = []
    summary = {"scanned": len(entries), "identified": 0, "unknown": 0,
               "with_vulns": 0, "worst_tier": None}
    tier_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    for e in entries:
        srv = _resolve_registry_server(db, e["name"], e["hint"])
        if srv is None:
            summary["unknown"] += 1
            results.append({"name": e["name"], "hint": e["hint"],
                            "status": "unknown",
                            "note": "not found in the scored registry"})
            continue
        summary["identified"] += 1
        axes = {}
        if mv:
            for a in db.execute(select(McpLlmAxisScore).where(
                    McpLlmAxisScore.server_id == srv.server_id,
                    McpLlmAxisScore.model_version == mv)).scalars():
                axes[a.axis_name] = {"label": a.label, "p_top": a.p_top}
        vulns = server_vulns(db, srv.server_id)
        if vulns.get("count"):
            summary["with_vulns"] += 1
        tier = srv.risk_tier or (axes.get("overall_risk", {}) or {}).get("label")
        if tier and tier_rank.get(str(tier).upper(), 0) > \
                tier_rank.get(str(summary["worst_tier"]).upper(), 0):
            summary["worst_tier"] = tier
        results.append({
            "name": e["name"], "hint": e["hint"], "status": "scored",
            "server_id": srv.server_id, "registry_name": srv.name,
            "url": srv.url, "risk_tier": tier, "verdict": srv.verdict,
            "axes": axes,
            "vulns_status": vulns["status"],
            "vulns": vulns.get("vulns", []),
        })
    return {"summary": summary, "results": results}


@router.post("/scan")
def post_scan(body: ScanRequest, db: Session = Depends(get_session),
              principal: Principal = Depends(get_principal)) -> dict:
    entries = extract_servers(body.config)
    if not entries:
        raise HTTPException(status_code=400,
                            detail="no mcpServers found in the config")
    charge_lookup(db, principal, n=1)        # one lookup per scan (public cap)
    return scan_config(db, body.config)


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for i in range(60):
        s.add(McpServerRegistry(server_id=f"noise-{i}", name=f"noise-{i}",
                                url=f"https://github.com/other/noise-{i}",
                                risk_tier="LOW", verdict="LOW"))
    s.add(McpServerRegistry(server_id="s-fork", name="inspector-fork",
                            url="https://github.com/anthropics/mcp-inspector-fork",
                            risk_tier="LOW", verdict="LOW"))
    s.add(McpServerRegistry(server_id="s1", name="mcp-inspector",
                            url="https://github.com/anthropics/mcp-inspector",
                            risk_tier="HIGH", verdict="HIGH"))
    s.add(McpLlmAxisScore(id=1, server_id="s1", axis_name="auth_strength",
                          label="WEAK", model_version="v3"))
    s.commit()

    cfg = json.dumps({"mcpServers": {
        "inspector": {"command": "npx", "args": ["-y", "github.com/anthropics/mcp-inspector"]},
        "fork-miss": {"command": "npx", "args": ["-y", "github.com/anthropics/mcp-inspector-unrelated"]},
        "mystery": {"command": "node", "args": ["./local.js"]},
    }})
    entries = extract_servers(cfg)
    assert len(entries) == 3
    out = scan_config(s, cfg)
    assert out["summary"]["scanned"] == 3
    assert out["summary"]["identified"] == 1 and out["summary"]["unknown"] == 2
    scored = [r for r in out["results"] if r["status"] == "scored"][0]
    assert scored["server_id"] == "s1" and scored["risk_tier"] == "HIGH"
    assert scored["axes"]["auth_strength"]["label"] == "WEAK"
    unk_names = {r["name"] for r in out["results"] if r["status"] == "unknown"}
    assert unk_names == {"fork-miss", "mystery"}
    print("PASS")
