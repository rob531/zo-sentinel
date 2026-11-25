"""risk_tier_migration_ledger_report.py -- risk_tier migration ledger (read-only).

Reconciles the `risk_tier` stored on `mcp_server_registry` against the tier
implied by the latest `mcp_llm_axis_scores` `overall_risk` `p_top`, and emits a
migration ledger listing every server whose stored tier still disagrees with the
model-implied tier (i.e. the servers that still need a tier migration).

Pure part (stdlib only): build_ledger(rows), render_ledger(ledger).
Impure part: fetch_rows(session) reads the authoritative APP tables through the
REAL app data layer (`app.db.get_session` / `app.models`). Read-only: no DB
writes, no network calls, no duckdb, no CSV/JSON inputs.

Tier vocabulary (shared with the risk tier map, thresholds on a 0..100 scale):
    >= 75  trusted
    >= 55  low
    >= 35  moderate
    else   high
A stored `p_top` of 0..1 is read as a fraction and scaled to the same scale.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# (tier, minimum p_top on a 0..100 scale) -- highest threshold first.
TIER_THRESHOLDS = (
    ("trusted", 75.0),
    ("low", 55.0),
    ("moderate", 35.0),
)
DEFAULT_TIER = "high"
UNKNOWN_TIER = "unknown"

# Stored registry tiers -> the shared vocabulary above.
REGISTRY_TIER_VOCAB = {
    "TRUSTED_GENERAL": "trusted",
    "TRUSTED_RESEARCH": "trusted",
    "TRUSTED": "trusted",
    "LOW": "low",
    "ENTERPRISE_CONTROLLED": "low",
    "MEDIUM": "moderate",
    "CAUTION_LIMITED": "moderate",
    "HIGH": "high",
    "CRITICAL": "high",
    "HIGH_RISK_ISOLATED": "high",
    "KNOWN_THREAT": "high",
    "UNKNOWN": UNKNOWN_TIER,
    "UNASSESSED": UNKNOWN_TIER,
    "": UNKNOWN_TIER,
}

COLUMNS = ("server_id", "name", "registry_tier", "derived_tier", "p_top", "delta_tier")


# ---------------------------------------------------------------- pure helpers


def _pct(value: Any) -> Optional[float]:
    """Coerce a stored p_top to a 0..100 score (fractions are scaled up)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0:  # NaN or negative
        return None
    if f <= 1.0:
        f = f * 100.0
    return f


def derive_tier(p_top: Any) -> str:
    """Tier implied by an `overall_risk` p_top."""
    score = _pct(p_top)
    if score is None:
        return UNKNOWN_TIER
    for tier, threshold in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return DEFAULT_TIER


def registry_tier_vocab(registry_tier: Any) -> str:
    """Normalise a stored registry tier into the shared vocabulary."""
    key = ("" if registry_tier is None else str(registry_tier)).strip().upper()
    if key in REGISTRY_TIER_VOCAB:
        return REGISTRY_TIER_VOCAB[key]
    if key in ("TRUSTED", "LOW", "MODERATE", "HIGH"):
        return key.lower()
    return UNKNOWN_TIER


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------- pure core


def build_ledger(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the migration ledger from reconciled rows.

    Each input row: {server_id, name, registry_tier, p_top, scored_at}.
    Returns {generated_at, summary: {total, mismatched, tier_counts}, entries}.
    """
    entries: List[Dict[str, Any]] = []
    tier_counts: Dict[str, int] = {}
    mismatched = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        server_id = row.get("server_id")
        registry_raw = row.get("registry_tier")
        p_top = row.get("p_top")
        derived = derive_tier(p_top)
        stored = registry_tier_vocab(registry_raw)
        delta = stored != derived
        if delta:
            mismatched += 1
        tier_counts[derived] = tier_counts.get(derived, 0) + 1
        entries.append({
            "server_id": server_id,
            "name": row.get("name"),
            "registry_tier": registry_raw,
            "derived_tier": derived,
            "p_top": p_top,
            "delta_tier": delta,
        })

    entries.sort(key=lambda e: (0 if e["delta_tier"] else 1, str(e.get("server_id") or "")))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(entries),
            "mismatched": mismatched,
            "tier_counts": tier_counts,
        },
        "entries": entries,
    }


def render_ledger(ledger: Dict[str, Any]) -> str:
    """Render the migration ledger as Markdown."""
    ledger = ledger or {}
    summary = ledger.get("summary") or {}
    entries = ledger.get("entries") or []
    tier_counts = summary.get("tier_counts") or {}

    lines = [
        "# Risk Tier Migration Ledger",
        "",
        f"Generated at: {ledger.get('generated_at') or '-'}",
        "",
        f"Total servers: {summary.get('total', 0)}",
        f"Mismatched tiers: {summary.get('mismatched', 0)}",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    for e in entries:
        lines.append("| " + " | ".join([
            _cell(e.get("server_id")),
            _cell(e.get("name")),
            _cell(e.get("registry_tier")),
            _cell(e.get("derived_tier")),
            _cell(e.get("p_top")),
            "yes" if e.get("delta_tier") else "no",
        ]) + " |")

    lines.append("")
    lines.append("Tier counts: " + ", ".join(
        f"{tier}={count}" for tier, count in sorted(tier_counts.items())))
    mismatched_ids = [str(e.get("server_id")) for e in entries if e.get("delta_tier")]
    lines.append("Servers needing migration: " + (", ".join(mismatched_ids) if mismatched_ids else "none"))
    return "\n".join(lines)


def ledger_to_json(ledger: Dict[str, Any]) -> str:
    """Serialise the ledger (export only -- never an input)."""
    return json.dumps(ledger, indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------- data access


def fetch_rows(session: Session) -> List[Dict[str, Any]]:
    """Read registry tier + latest `overall_risk` p_top per server (read-only)."""
    registry = session.execute(
        select(McpServerRegistry.server_id,
               McpServerRegistry.name,
               McpServerRegistry.risk_tier)
    ).all()

    scores = session.execute(
        select(McpLlmAxisScore.server_id,
               McpLlmAxisScore.p_top,
               McpLlmAxisScore.scored_at)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
    ).all()

    newest: Dict[str, Dict[str, Any]] = {}
    for server_id, p_top, scored_at in scores:
        current = newest.get(server_id)
        if current is None:
            newest[server_id] = {"p_top": p_top, "scored_at": scored_at}
            continue
        cur_ts = current.get("scored_at")
        if cur_ts is None or (scored_at is not None and scored_at > cur_ts):
            newest[server_id] = {"p_top": p_top, "scored_at": scored_at}

    rows: List[Dict[str, Any]] = []
    for server_id, name, risk_tier in registry:
        latest = newest.get(server_id) or {}
        scored_at = latest.get("scored_at")
        rows.append({
            "server_id": server_id,
            "name": name,
            "registry_tier": risk_tier,
            "p_top": latest.get("p_top"),
            "scored_at": scored_at.isoformat() if scored_at else None,
        })
    return rows


@contextmanager
def _session_scope() -> Iterator[Session]:
    """One real app session (read-only use), closed on exit."""
    gen = get_session()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
        session.close()


def build_report(session: Optional[Session] = None) -> Dict[str, Any]:
    """Build the ledger from the app data layer (read-only)."""
    if session is not None:
        return build_ledger(fetch_rows(session))
    try:
        with _session_scope() as s:
            return build_ledger(fetch_rows(s))
    except Exception:
        return build_ledger([])


def main(rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """Print the migration ledger report and return its text."""
    ledger = build_ledger(rows) if rows is not None else build_report()
    body = render_ledger(ledger)
    print(body)
    return body


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session for the DB path
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # --- pure core over 4 known-good rows (2 matching, 2 mismatched) ---
    ROWS = [
        {"server_id": "srv_match_trusted", "name": "Trusted MCP",
         "registry_tier": "TRUSTED_GENERAL", "p_top": 90.0,
         "scored_at": "2026-11-01T00:00:00+00:00"},
        {"server_id": "srv_match_high", "name": "Risky MCP",
         "registry_tier": "HIGH", "p_top": 20.0,
         "scored_at": "2026-11-01T00:00:00+00:00"},
        {"server_id": "srv_mismatch_up", "name": "Stale Low MCP",
         "registry_tier": "LOW", "p_top": 10.0,
         "scored_at": "2026-11-01T00:00:00+00:00"},
        {"server_id": "srv_mismatch_down", "name": "Stale High MCP",
         "registry_tier": "HIGH", "p_top": 80.0,
         "scored_at": "2026-11-01T00:00:00+00:00"},
    ]

    ledger = build_ledger(ROWS)
    assert ledger["summary"]["total"] == 4, ledger
    assert ledger["summary"]["mismatched"] == 2, ledger
    by_id = {e["server_id"]: e for e in ledger["entries"]}
    assert by_id["srv_match_trusted"]["derived_tier"] == "trusted", ledger
    assert by_id["srv_match_trusted"]["delta_tier"] is False, ledger
    assert by_id["srv_match_high"]["derived_tier"] == "high", ledger
    assert by_id["srv_mismatch_up"]["delta_tier"] is True, ledger
    assert by_id["srv_mismatch_down"]["derived_tier"] == "trusted", ledger
    assert ledger["summary"]["tier_counts"]["trusted"] == 2, ledger
    assert ledger["summary"]["tier_counts"]["high"] == 2, ledger

    md = render_ledger(ledger)
    assert isinstance(md, str) and md, md
    assert "srv_mismatch_up" in md, md
    assert "srv_mismatch_down" in md, md
    assert "Mismatched tiers: 2" in md, md

    # --- edge cases: empty input, missing p_top, fractional p_top ---
    empty = build_ledger([])
    assert empty["summary"]["total"] == 0 and empty["summary"]["mismatched"] == 0, empty
    assert isinstance(render_ledger(empty), str)
    no_score = build_ledger([{"server_id": "srv_none", "name": "Unscored",
                              "registry_tier": "HIGH", "p_top": None}])
    assert no_score["entries"][0]["derived_tier"] == "unknown", no_score
    frac = derive_tier(0.9)
    assert frac == "trusted", frac
    assert derive_tier(0.6) == "low", derive_tier(0.6)
    assert derive_tier(0.4) == "moderate", derive_tier(0.4)
    assert derive_tier(0.1) == "high", derive_tier(0.1)

    # --- real data layer over an in-memory SQLite session ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="github", risk_tier="HIGH"))
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.commit()

    db_rows = fetch_rows(s)
    assert len(db_rows) == 1, db_rows
    assert db_rows[0]["registry_tier"] == "HIGH", db_rows
    db_ledger = build_ledger(db_rows)
    assert db_ledger["summary"]["total"] == 1, db_ledger
    s.close()

    print(main(ROWS))
    print("PASS")
