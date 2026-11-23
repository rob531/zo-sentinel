"""unauth_access_risk_overlay_report.py -- overlay unauthenticated-access findings
onto current registry risk state.

Analysts need to know which RISKY servers are ALSO reachable without auth. This
module overlays the isolated live-probe exposure table (mcp_unauth_access) onto the
registry's published risk state (mcp_server_registry).

The overlay computation is kept in PURE functions (build_report / render_text) so it
is unit-testable without a database; the row fetchers read through the REAL app
data layer (app.db / app.models). Read-only: no writes, no network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session  # noqa: F401  (real data-layer seam)
from app.models import McpServerRegistry, McpUnauthAccess  # noqa: F401

# --- unauth_access vocabulary -----------------------------------------------
# Exposure (reachable without auth) modes vs. non-exposure modes.
EXPOSURE_MODES = frozenset({
    "OPEN", "OPEN_EXEC", "OPEN_BENIGN", "UNAUTH", "UNAUTHENTICATED",
    "EXPOSED", "UNSAFE", "TRUE", "YES", "1",
})
NON_EXPOSURE_MODES = frozenset({
    "GATED", "UNREACHABLE", "NONE", "SAFE", "FALSE", "NO", "0", "",
})
UNKNOWN_MODE = "UNKNOWN"

# --- risk-tier severity ranking (higher = more severe) ---------------------
TIER_RANK: Dict[str, int] = {
    "KNOWN_THREAT": 7, "CRITICAL": 7,
    "HIGH_RISK_ISOLATED": 6, "HIGH": 6,
    "CAUTION_LIMITED": 5,
    "ENTERPRISE_CONTROLLED": 4, "MEDIUM": 4,
    "TRUSTED_RESEARCH": 3,
    "TRUSTED_GENERAL": 2, "LOW": 2,
    "INSUFFICIENT": 1, "UNASSESSED": 1, "PENDING": 1,
}


def _mode(value: Any) -> str:
    """Normalise an unauth_access value to an upper-case mode token."""
    if value is None:
        return UNKNOWN_MODE
    text = str(value).strip().upper()
    return text or UNKNOWN_MODE


def _is_exposure(mode: str) -> bool:
    if mode in NON_EXPOSURE_MODES:
        return False
    if mode in EXPOSURE_MODES:
        return True
    return mode.startswith("OPEN")


def tier_rank(tier: Any) -> int:
    """Severity rank of a risk tier; unknown tiers rank lowest."""
    if tier is None:
        return 0
    return TIER_RANK.get(str(tier).strip().upper(), 0)


def _registry_index(registry_rows: List[dict]) -> Dict[str, dict]:
    index: Dict[str, dict] = {}
    for row in registry_rows or []:
        sid = str(row.get("server_id") or "")
        if sid:
            index.setdefault(sid, row)
    return index


def build_report(registry_rows: List[dict], unauth_rows: List[dict]) -> dict:
    """Overlay unauthenticated-access findings onto registry risk state.

    registry_rows: dicts from mcp_server_registry with keys server_id, name,
        registry_source, risk_tier, verdict, trust_score.
    unauth_rows: dicts from mcp_unauth_access with keys server_id, url,
        unauth_access, source, updated_at.
    """
    reg = _registry_index(registry_rows)

    # Latest exposure row per server wins (unauth_rows arrive newest-first).
    latest: Dict[str, dict] = {}
    for row in unauth_rows or []:
        sid = str(row.get("server_id") or "")
        if not sid:
            continue
        if sid not in latest:
            latest[sid] = row

    servers_with_unauth: List[dict] = []
    by_unauth_mode: Dict[str, int] = {}
    by_risk_tier: Dict[str, int] = {}

    for sid, urow in latest.items():
        mode = _mode(urow.get("unauth_access"))
        if not _is_exposure(mode):
            continue
        rrow = reg.get(sid, {})
        tier = rrow.get("risk_tier")
        tier_key = str(tier).strip().upper() if tier is not None else "UNASSESSED"
        entry = {
            "server_id": sid,
            "name": rrow.get("name"),
            "risk_tier": tier,
            "verdict": rrow.get("verdict"),
            "unauth_access": mode,
            "url": urow.get("url"),
        }
        servers_with_unauth.append(entry)
        by_unauth_mode[mode] = by_unauth_mode.get(mode, 0) + 1
        by_risk_tier[tier_key] = by_risk_tier.get(tier_key, 0) + 1

    servers_with_unauth.sort(key=lambda e: (-tier_rank(e["risk_tier"]), e["server_id"]))

    registry_total = len(reg)
    exposed_ids = {e["server_id"] for e in servers_with_unauth}
    without_unauth = sum(1 for sid in reg if sid not in exposed_ids)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry_servers": registry_total,
        "servers_with_unauth": len(servers_with_unauth),
        "by_unauth_mode": by_unauth_mode,
        "by_risk_tier": by_risk_tier,
        "high_risk_unauth_servers": servers_with_unauth,
        "registry_servers_without_unauth": without_unauth,
    }


def render_text(report: dict) -> str:
    """Plain-text summary of the overlay report (includes the high-risk section)."""
    rep = report or {}
    high = rep.get("high_risk_unauth_servers") or []
    lines = [
        "UNAUTHENTICATED-ACCESS RISK OVERLAY",
        "=" * 60,
        f"Generated at:            {rep.get('generated_at', 'n/a')}",
        f"Registry servers:        {rep.get('registry_servers', 0)}",
        f"Servers with unauth:     {rep.get('servers_with_unauth', 0)}",
        f"Registry servers w/o unauth: {rep.get('registry_servers_without_unauth', 0)}",
        "",
        "UNAUTH MODE BREAKDOWN",
    ]
    by_mode = rep.get("by_unauth_mode") or {}
    if by_mode:
        for mode in sorted(by_mode):
            lines.append(f"  {mode:<16} {by_mode[mode]}")
    else:
        lines.append("  (none)")

    lines += ["", "RISK TIER BREAKDOWN"]
    by_tier = rep.get("by_risk_tier") or {}
    if by_tier:
        for tier in sorted(by_tier, key=lambda t: (-tier_rank(t), t)):
            lines.append(f"  {tier:<24} {by_tier[tier]}")
    else:
        lines.append("  (none)")

    lines += ["", "HIGH-RISK SERVERS WITH UNAUTHENTICATED ACCESS"]
    if high:
        for entry in high:
            lines.append(
                f"  [{entry.get('risk_tier') or 'UNASSESSED'}] {entry.get('server_id')}"
                f" | {entry.get('name') or ''}"
            )
            lines.append(
                f"      mode={entry.get('unauth_access')} url={entry.get('url') or 'n/a'}"
                f" verdict={entry.get('verdict') or 'n/a'}"
            )
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# --- data layer (real app.db / app.models reads) -----------------------------

def fetch_registry_rows(db: Session) -> List[dict]:
    """Read the registry risk state rows."""
    stmt = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
        McpServerRegistry.trust_score,
    )
    return [
        {
            "server_id": r[0],
            "name": r[1],
            "registry_source": r[2],
            "risk_tier": r[3],
            "verdict": r[4],
            "trust_score": r[5],
        }
        for r in db.execute(stmt).all()
    ]


def fetch_unauth_rows(db: Session) -> List[dict]:
    """Read the unauthenticated-access rows (newest first)."""
    stmt = select(
        McpUnauthAccess.server_id,
        McpUnauthAccess.url,
        McpUnauthAccess.unauth_access,
        McpUnauthAccess.source,
        McpUnauthAccess.updated_at,
    ).order_by(McpUnauthAccess.updated_at.desc())
    rows = []
    for r in db.execute(stmt).all():
        updated = r[4]
        rows.append({
            "server_id": r[0],
            "url": r[1],
            "unauth_access": r[2],
            "source": r[3],
            "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
        })
    return rows


def build_report_from_db(db: Session) -> dict:
    """Overlay report over the live app-plane tables (read-only)."""
    return build_report(fetch_registry_rows(db), fetch_unauth_rows(db))


def main() -> int:
    """Build the overlay report from the live data layer and print the text form."""
    db = SessionLocal()
    try:
        report = build_report_from_db(db)
    finally:
        db.close()
    print(render_text(report))
    return 0


if __name__ == "__main__":
    # -- acceptance seed: 3 registry rows, 2 unauth rows ------------------
    registry_rows = [
        {"server_id": "srv_crit", "name": "Critical Exposed MCP", "registry_source": "github",
         "risk_tier": "critical", "verdict": "BLOCK", "trust_score": 0.2},
        {"server_id": "srv_low", "name": "Low Risk Exposed MCP", "registry_source": "npm",
         "risk_tier": "low", "verdict": "ALLOW", "trust_score": 0.9},
        {"server_id": "srv_clean", "name": "Clean MCP", "registry_source": "github",
         "risk_tier": "medium", "verdict": "ALLOW", "trust_score": 0.7},
    ]
    unauth_rows = [
        {"server_id": "srv_crit", "url": "https://crit.example/mcp",
         "unauth_access": "OPEN_EXEC", "source": "probe", "updated_at": "2026-01-02T00:00:00Z"},
        {"server_id": "srv_low", "url": "https://low.example/mcp",
         "unauth_access": "OPEN", "source": "probe", "updated_at": "2026-01-01T00:00:00Z"},
    ]

    report = build_report(registry_rows, unauth_rows)
    assert report["servers_with_unauth"] == 2, report
    assert report["by_unauth_mode"] == {"OPEN_EXEC": 1, "OPEN": 1}, report
    assert report["by_risk_tier"] == {"CRITICAL": 1, "LOW": 1}, report
    assert len(report["high_risk_unauth_servers"]) == 2, report
    assert report["high_risk_unauth_servers"][0]["server_id"] == "srv_crit", report
    assert report["high_risk_unauth_servers"][1]["server_id"] == "srv_low", report
    assert report["registry_servers_without_unauth"] == 1, report
    text = render_text(report)
    assert "unauth" in text.lower(), text
    assert "srv_crit" in text and "srv_low" in text, text

    # -- edge case: gated / unreachable rows are NOT exposure -------------
    gated = build_report(
        registry_rows,
        unauth_rows + [
            {"server_id": "srv_clean", "url": "https://clean.example/mcp",
             "unauth_access": "GATED", "source": "probe", "updated_at": None},
        ],
    )
    assert gated["servers_with_unauth"] == 2, gated
    assert gated["registry_servers_without_unauth"] == 1, gated
    assert build_report([], [])["servers_with_unauth"] == 0
    assert build_report([], [])["high_risk_unauth_servers"] == []

    # -- real data layer round-trip (in-memory SQLite, real models) --------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    McpServerRegistry.metadata.create_all(
        eng, tables=[McpServerRegistry.__table__, McpUnauthAccess.__table__])
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="db_crit", name="DB Critical",
                            registry_source="github", risk_tier="CRITICAL",
                            verdict="BLOCK"))
    s.add(McpServerRegistry(server_id="db_ok", name="DB Clean",
                            registry_source="npm", risk_tier="LOW",
                            verdict="ALLOW"))
    s.add(McpUnauthAccess(server_id="db_crit", url="https://dbc.example/mcp",
                          unauth_access="OPEN_EXEC", source="probe"))
    s.commit()

    live = build_report_from_db(s)
    assert live["servers_with_unauth"] == 1, live
    assert live["high_risk_unauth_servers"][0]["server_id"] == "db_crit", live
    assert live["registry_servers_without_unauth"] == 1, live
    s.close()

    print("PASS")
