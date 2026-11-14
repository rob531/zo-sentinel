"""unauth_access_gap_report.py -- unauthenticated-access exposure gap report.

Reads the live-probe EXPOSURE table `mcp_unauth_access` (server_id, url,
unauth_access, source, updated_at) joined to `mcp_server_registry`
(server_id, name, risk_tier, trust_score, verdict) through the REAL app
SQLAlchemy session (app.db / app.models) -- read-only: no INSERT/UPDATE/
DELETE, no network, no duckdb, no CSV/JSON inputs.

`generate_report(days, registry_source)` returns the gap report dict;
`main()` prints a Markdown table and the JSON payload to stdout.

Exposure is a MEASURED FACT (did the endpoint serve tools unauthenticated?),
so it is deliberately NOT gated by trust_gate (same reasoning as
unauth_access.py) -- gating it would hide a true property of an official
publisher. The registry risk tier is reported verbatim and
`published_risk_tier` additionally carries the trust-gated view.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api/reports", tags=["reports"])

DEFAULT_WINDOW_DAYS = 30
STALE_DAYS = 30

# exposure labels: a served-but-refused / dark endpoint is NOT exposure
NON_EXPOSURE = {"GATED", "UNREACHABLE"}

_ROWS_SQL_ALL = text("""
SELECT u.server_id, u.url, u.unauth_access, u.source, u.updated_at,
       r.name, r.risk_tier, r.trust_score, r.verdict, r.registry_source
FROM mcp_unauth_access u
LEFT JOIN mcp_server_registry r ON r.server_id = u.server_id
WHERE (u.updated_at IS NULL OR u.updated_at >= :since)
ORDER BY u.updated_at DESC
""")

_ROWS_SQL_SOURCE = text("""
SELECT u.server_id, u.url, u.unauth_access, u.source, u.updated_at,
       r.name, r.risk_tier, r.trust_score, r.verdict, r.registry_source
FROM mcp_unauth_access u
JOIN mcp_server_registry r ON r.server_id = u.server_id
WHERE (u.updated_at IS NULL OR u.updated_at >= :since)
  AND r.registry_source = :registry_source
ORDER BY u.updated_at DESC
""")

_TOTAL_SQL_ALL = text("SELECT count(*) FROM mcp_server_registry")
_TOTAL_SQL_SOURCE = text(
    "SELECT count(*) FROM mcp_server_registry WHERE registry_source = :registry_source")


# ---------------------------------------------------------------- helpers


def _as_utc(value: Any) -> Optional[datetime]:
    """Coerce a datetime / ISO string into an aware UTC datetime (None if absent)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _cell(value: Any) -> str:
    """Markdown-safe cell value."""
    if value is None or value == "":
        return "-"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _is_exposure(value: Any) -> bool:
    """True when the stored label denotes a served-unauthenticated endpoint."""
    if value is None:
        return False
    label = str(value).strip().upper()
    if not label:
        return False
    return label not in NON_EXPOSURE


def _published_tier(url: Any, name: Any, risk_tier: Any) -> str:
    """Registry tier verbatim, plus the trust-gated view for verified publishers."""
    tier = risk_tier or "-"
    gate = trust_gate(url, name, {"overall_risk": (risk_tier or "").upper()})
    published = (gate.get("published_overall_risk") or "").upper()
    if gate.get("capped") and published:
        return published
    return str(tier)


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------- data access


def fetch_rows(db: Session, days: int = DEFAULT_WINDOW_DAYS,
               registry_source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Joined exposure rows inside the `days` window (read-only)."""
    window = max(0, int(days))
    since = datetime.now(timezone.utc) - timedelta(days=window)
    if registry_source:
        stmt = _ROWS_SQL_SOURCE
        params = {"since": since, "registry_source": registry_source}
    else:
        stmt = _ROWS_SQL_ALL
        params = {"since": since}
    out: List[Dict[str, Any]] = []
    for r in db.execute(stmt, params).all():
        out.append({
            "server_id": r[0],
            "url": r[1],
            "unauth_access": r[2],
            "source": r[3],
            "updated_at": _iso(r[4]),
            "name": r[5],
            "risk_tier": r[6],
            "trust_score": r[7],
            "verdict": r[8],
            "registry_source": r[9],
        })
    return out


def fetch_registry_total(db: Session, registry_source: Optional[str] = None) -> int:
    """Number of registry servers (denominator of the exposure percentage)."""
    if registry_source:
        row = db.execute(_TOTAL_SQL_SOURCE, {"registry_source": registry_source}).first()
    else:
        row = db.execute(_TOTAL_SQL_ALL).first()
    return int(row[0]) if row and row[0] is not None else 0


# ---------------------------------------------------------------- report core


def build_report(rows: List[Dict[str, Any]], servers_total: int,
                 days: int = DEFAULT_WINDOW_DAYS,
                 registry_source: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the gap report from the joined rows (pure)."""
    reference = datetime.now(timezone.utc)
    stale_cutoff = reference - timedelta(days=STALE_DAYS)

    tier_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    exposure_servers = set()
    stale: List[Dict[str, Any]] = []
    out_rows: List[Dict[str, Any]] = []

    for row in rows or []:
        server_id = row.get("server_id")
        if server_id is None:
            continue
        exposed = _is_exposure(row.get("unauth_access"))
        if exposed:
            exposure_servers.add(server_id)
            tier = row.get("risk_tier") or "UNKNOWN"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            src = row.get("source") or "UNKNOWN"
            source_counts[src] = source_counts.get(src, 0) + 1

        updated = _as_utc(row.get("updated_at"))
        if updated is not None and updated < stale_cutoff:
            stale.append({"server_id": server_id, "name": row.get("name"),
                          "updated_at": row.get("updated_at")})

        out_rows.append({
            "server_id": server_id,
            "name": row.get("name"),
            "url": row.get("url"),
            "unauth_access": row.get("unauth_access"),
            "risk_tier": row.get("risk_tier"),
            "published_risk_tier": _published_tier(row.get("url"), row.get("name"),
                                                   row.get("risk_tier")),
            "updated_at": row.get("updated_at"),
        })

    servers_with_unauth = len(exposure_servers)
    total = int(servers_total or 0)
    exposure_pct = round(servers_with_unauth * 100.0 / total, 2) if total else 0.0

    return {
        "generated_at": reference.isoformat(),
        "window_days": max(0, int(days)),
        "registry_source": registry_source,
        "totals": {
            "servers_with_unauth": servers_with_unauth,
            "servers_total": total,
            "exposure_pct": exposure_pct,
        },
        "by_tier": [{"risk_tier": t, "unauth_count": c}
                    for t, c in sorted(tier_counts.items())],
        "by_source": [{"source": s, "unauth_count": c}
                      for s, c in sorted(source_counts.items())],
        "stale": stale,
        "rows": out_rows,
    }


def generate_report(days: int = DEFAULT_WINDOW_DAYS,
                    registry_source: Optional[str] = None,
                    db: Optional[Session] = None) -> Dict[str, Any]:
    """Build the unauthenticated-access gap report from the real app tables."""
    if db is not None:
        rows = fetch_rows(db, days=days, registry_source=registry_source)
        total = fetch_registry_total(db, registry_source=registry_source)
        return build_report(rows, total, days=days, registry_source=registry_source)
    with _session_scope() as session:
        rows = fetch_rows(session, days=days, registry_source=registry_source)
        total = fetch_registry_total(session, registry_source=registry_source)
        return build_report(rows, total, days=days, registry_source=registry_source)


@router.get("/unauth-access-gap")
def unauth_access_gap(days: int = DEFAULT_WINDOW_DAYS,
                      registry_source: Optional[str] = None,
                      db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Unauthenticated-access exposure gap report over the real app tables."""
    return generate_report(days=days, registry_source=registry_source, db=db)


# ---------------------------------------------------------------- entrypoint


def render_markdown(report: Dict[str, Any]) -> str:
    """Markdown table of the audited exposure rows."""
    totals = report.get("totals") or {}
    lines = [
        "# Unauthenticated Access Gap Report",
        "",
        (f"Window: {report.get('window_days')}d | "
         f"registry_source: {report.get('registry_source') or 'all'}"),
        "",
        (f"Servers with unauth access: {totals.get('servers_with_unauth', 0)} / "
         f"{totals.get('servers_total', 0)} "
         f"({totals.get('exposure_pct', 0.0)}%)"),
        "",
        "| server_id | name | unauth_access | risk_tier | updated_at |",
        "|---|---|---|---|---|",
    ]
    for row in report.get("rows") or []:
        lines.append("| " + " | ".join([
            _cell(row.get("server_id")),
            _cell(row.get("name")),
            _cell(row.get("unauth_access")),
            _cell(row.get("risk_tier")),
            _cell(row.get("updated_at")),
        ]) + " |")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the Markdown table and the JSON report to stdout."""
    parser = argparse.ArgumentParser(description="Unauthenticated access gap report")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help="window applied to mcp_unauth_access.updated_at")
    parser.add_argument("--registry-source", default=None,
                        help="optional registry_source filter")
    args = parser.parse_args(argv)

    report = generate_report(days=args.days, registry_source=args.registry_source)
    print(render_markdown(report))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    NOW = datetime.now(timezone.utc)

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    # 4 registry servers (one of them never probed)
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            registry_source="github",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="HIGH", trust_score=0.9, verdict="high"))
    s.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                            registry_source="npmjs",
                            url="https://www.npmjs.com/package/beta-mcp",
                            risk_tier="MEDIUM", trust_score=0.5, verdict="medium"))
    s.add(McpServerRegistry(server_id="srv3", name="Gamma MCP",
                            registry_source="github",
                            url="https://github.com/acme/gamma-mcp",
                            risk_tier="LOW", trust_score=0.2, verdict="low"))
    s.add(McpServerRegistry(server_id="srv4", name="Delta MCP",
                            registry_source="github",
                            url="https://github.com/acme/delta-mcp",
                            risk_tier="HIGH", trust_score=0.4, verdict="high"))
    # 3 unauth-access rows
    s.add(McpUnauthAccess(server_id="srv1", url="https://github.com/stripe/agent-toolkit",
                          unauth_access="OPEN_EXEC", source="lpg_auth_ground",
                          updated_at=(NOW - timedelta(days=5)).replace(tzinfo=None)))
    s.add(McpUnauthAccess(server_id="srv2", url="https://www.npmjs.com/package/beta-mcp",
                          unauth_access="OPEN_BENIGN", source="lpg_auth_ground",
                          updated_at=(NOW - timedelta(days=60)).replace(tzinfo=None)))
    s.add(McpUnauthAccess(server_id="srv3", url="https://github.com/acme/gamma-mcp",
                          unauth_access="OPEN", source="lpg_auth_ground",
                          updated_at=(NOW - timedelta(hours=2)).replace(tzinfo=None)))
    s.commit()

    report = generate_report(days=365, db=s)
    assert report["totals"]["servers_with_unauth"] == 3, report
    assert report["totals"]["servers_total"] == 4, report
    assert report["totals"]["exposure_pct"] == 75.0, report
    assert len(report["by_tier"]) >= 1, report
    assert len(report["rows"]) == 3, report
    assert all("server_id" in r and "risk_tier" in r for r in report["rows"]), report
    assert report["by_source"] == [{"source": "lpg_auth_ground", "unauth_count": 3}], report
    assert {r["risk_tier"] for r in report["by_tier"]} == {"HIGH", "MEDIUM", "LOW"}, report
    assert len(report["stale"]) == 1 and report["stale"][0]["server_id"] == "srv2", report
    # trust_gate: verified publisher's HIGH tier is capped in the published view
    stripe_row = next(r for r in report["rows"] if r["server_id"] == "srv1")
    assert stripe_row["risk_tier"] == "HIGH", stripe_row
    assert stripe_row["published_risk_tier"] == "MEDIUM", stripe_row

    # edge: narrow window -> only the freshly probed server
    narrow = generate_report(days=1, db=s)
    assert narrow["totals"]["servers_with_unauth"] == 1, narrow
    assert len(narrow["rows"]) == 1, narrow

    # edge: registry_source filter (bound parameter)
    filtered = generate_report(days=365, registry_source="npmjs", db=s)
    assert filtered["totals"]["servers_total"] == 1, filtered
    assert filtered["totals"]["servers_with_unauth"] == 1, filtered
    assert [r["server_id"] for r in filtered["rows"]] == ["srv2"], filtered

    # edge: zero window -> nothing in range
    empty = generate_report(days=0, db=s)
    assert empty["totals"]["servers_with_unauth"] == 0, empty
    assert empty["rows"] == [], empty

    # endpoint over the REAL data layer (SQLite override of get_session)
    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    resp = client.get("/api/reports/unauth-access-gap", params={"days": 365})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["servers_with_unauth"] == 3, body
    assert len(body["by_tier"]) >= 1, body
    assert all("server_id" in r and "risk_tier" in r for r in body["rows"]), body

    s.close()
    print("PASS")
