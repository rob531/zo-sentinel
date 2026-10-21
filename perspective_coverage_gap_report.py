"""perspective_coverage_gap_report.py -- blind-spot report for saved perspectives.

Finds servers that are visible in the fleet (mcp_server_registry) but never
appear in ANY perspective membership (perspectives / perspective_snapshots) --
i.e. the servers an analyst's saved perspectives are blind to.

Standalone report generator (no HTTP route):
    generate_report(conn) -> dict
    summarize(result: dict) -> str
    main() -> int

Data access is READ-ONLY and goes through the app SQLAlchemy session
(app.db.get_session + app.models). The report core is pure stdlib so it can be
driven from any connector that yields the three row sets.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveSnapshot

MAX_SAMPLE = 25


# ---------------------------------------------------------------- connectors

class SessionConnector:
    """Real connector: reads the APP plane via the app.db session."""

    def __init__(self, db: Session):
        self.db = db

    def servers(self) -> List[Any]:
        return list(self.db.execute(select(McpServerRegistry)).scalars().all())

    def perspectives(self) -> List[Any]:
        return list(self.db.execute(select(Perspective)).scalars().all())

    def snapshots(self) -> List[Any]:
        return list(self.db.execute(select(PerspectiveSnapshot)).scalars().all())


def _call(conn: Any, name: str) -> List[Any]:
    """Fetch one row set from a connector (method or plain attribute)."""
    fn = getattr(conn, name, None)
    if fn is None:
        return []
    rows = fn() if callable(fn) else fn
    return list(rows or [])


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Row field access that tolerates ORM objects, dicts and sequences."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    val = getattr(row, key, default)
    if val is None and isinstance(row, (list, tuple)) and len(row) > 0:
        return row[0]
    return val


# ------------------------------------------------------------- membership

def _ids_from_seq(items: Iterable[Any]) -> Set[str]:
    out: Set[str] = set()
    for item in items:
        if isinstance(item, str):
            if item:
                out.add(item)
        elif isinstance(item, dict):
            sid = item.get("server_id") or item.get("id")
            if isinstance(sid, str) and sid:
                out.add(sid)
    return out


def extract_server_ids(blob: Any) -> Set[str]:
    """Normalise a membership blob into a set of server_ids.

    Tolerates: JSON list of ids, {server_id: risk_tier} mapping, a dict whose
    values are lists/dicts of members, and a JSON string of any of those.
    A malformed blob yields an empty set -- it never raises.
    """
    try:
        return _extract_server_ids(blob)
    except Exception:
        return set()


def _extract_server_ids(blob: Any) -> Set[str]:
    if blob is None:
        return set()
    if isinstance(blob, str):
        text = blob.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            return set()
        return _extract_server_ids(parsed)
    if isinstance(blob, dict):
        values = list(blob.values())
        if values and all(isinstance(v, str) for v in values):
            return {k for k in blob.keys() if isinstance(k, str) and k}
        ids: Set[str] = set()
        for v in values:
            if isinstance(v, (list, tuple, set)):
                ids |= _ids_from_seq(v)
            elif isinstance(v, dict):
                ids |= _ids_from_seq([v])
            elif isinstance(v, str):
                ids |= {v} if v else set()
        return ids
    if isinstance(blob, (list, tuple, set)):
        return _ids_from_seq(blob)
    return set()


# ---------------------------------------------------------------- report

def generate_report(conn: Any) -> dict:
    """Build the coverage-gap report from a connector (read-only)."""
    if conn is None:
        raise ValueError("a connector is required")
    if not hasattr(conn, "servers") and hasattr(conn, "execute"):
        conn = SessionConnector(conn)

    servers = _call(conn, "servers")
    perspectives = _call(conn, "perspectives")
    snapshots = _call(conn, "snapshots")

    covered_ids: Set[str] = set()
    for snap in snapshots:
        covered_ids |= extract_server_ids(_row_get(snap, "membership"))

    uncovered_by_tier: dict = {}
    sample: List[dict] = []
    uncovered = 0
    covered = 0
    for srv in servers:
        sid = _row_get(srv, "server_id")
        if not isinstance(sid, str) or not sid:
            continue
        if sid in covered_ids:
            covered += 1
            continue
        uncovered += 1
        tier = _row_get(srv, "risk_tier") or "UNKNOWN"
        tier = str(tier)
        uncovered_by_tier[tier] = uncovered_by_tier.get(tier, 0) + 1
        if len(sample) < MAX_SAMPLE:
            sample.append({
                "server_id": sid,
                "name": _row_get(srv, "name"),
                "risk_tier": _row_get(srv, "risk_tier"),
            })

    total = covered + uncovered
    coverage_pct = round((covered / total) * 100.0, 2) if total else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total,
        "covered_servers": covered,
        "uncovered_servers": uncovered,
        "coverage_pct": coverage_pct,
        "uncovered_by_tier": uncovered_by_tier,
        "sample_uncovered": sample,
    }


def summarize(result: dict) -> str:
    """Human-readable summary of a report dict."""
    result = result or {}
    tiers = result.get("uncovered_by_tier") or {}
    tier_bits = ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())) or "none"
    lines = [
        "Perspective coverage gap report",
        f"  generated_at:   {result.get('generated_at')}",
        f"  total servers: {result.get('total_servers', 0)}",
        f"  covered:       {result.get('covered_servers', 0)}",
        f"  uncovered:     {result.get('uncovered_servers', 0)}",
        f"  coverage_pct:  {result.get('coverage_pct', 0.0)}",
        f"  uncovered by tier: {tier_bits}",
    ]
    sample = result.get("sample_uncovered") or []
    if sample:
        lines.append("  sample uncovered:")
        for row in sample[:MAX_SAMPLE]:
            lines.append(
                f"    - {row.get('server_id')} "
                f"[{row.get('risk_tier') or 'UNKNOWN'}] {row.get('name') or ''}".rstrip())
    else:
        lines.append("  no uncovered servers")
    return "\n".join(lines)


def main() -> int:
    """Run the report against the live app DB (read-only)."""
    try:
        db = next(get_session())
    except Exception as exc:
        print(f"coverage-gap report unavailable: {exc}")
        return 1
    try:
        result = generate_report(SessionConnector(db))
    except Exception as exc:
        print(f"coverage-gap report failed: {exc}")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass
    print(summarize(result))
    return 0


# ---------------------------------------------------------------- self-test

class _FakeConnector:
    """In-memory connector for the __main__ self-test."""

    def __init__(self, servers, perspectives, snapshots):
        self._servers = servers
        self._perspectives = perspectives
        self._snapshots = snapshots

    def servers(self):
        return self._servers

    def perspectives(self):
        return self._perspectives

    def snapshots(self):
        return self._snapshots


def _self_test() -> None:
    servers = [
        {"server_id": "srv_high", "name": "High Risk MCP", "risk_tier": "HIGH_RISK_ISOLATED",
         "verdict": "high", "last_assessed": None},
        {"server_id": "srv_a", "name": "A MCP", "risk_tier": "CAUTION_LIMITED",
         "verdict": "caution", "last_assessed": None},
        {"server_id": "srv_b", "name": "B MCP", "risk_tier": "AMBER_UNVERIFIED",
         "verdict": "amber", "last_assessed": None},
        {"server_id": "srv_c", "name": "C MCP", "risk_tier": "TRUSTED_GENERAL",
         "verdict": "trusted", "last_assessed": None},
    ]
    perspectives = [{"id": "p1", "name": "Watchlist"}]
    snapshots = [
        {"perspective_id": "p1",
         "membership": ["srv_a", "srv_b", "srv_c"]},
        {"perspective_id": "p1", "membership": "<<malformed>>"},
    ]
    conn = _FakeConnector(servers, perspectives, snapshots)
    result = generate_report(conn)

    assert result["total_servers"] == 4, result
    assert result["covered_servers"] == 3, result
    assert result["uncovered_servers"] == 1, result
    assert result["uncovered_by_tier"]["HIGH_RISK_ISOLATED"] == 1, result
    assert 0 <= result["coverage_pct"] <= 100, result
    assert result["sample_uncovered"][0]["server_id"] == "srv_high", result
    assert result["generated_at"], result

    # malformed membership blobs never raise and contribute no members
    assert extract_server_ids("<<malformed>>") == set()
    assert extract_server_ids(None) == set()
    assert extract_server_ids({"srv_x": "HIGH_RISK_ISOLATED"}) == {"srv_x"}
    assert extract_server_ids({"members": ["srv_y"]}) == {"srv_y"}
    assert extract_server_ids({"srv_z": {"server_id": "srv_z"}}) == {"srv_z"}

    # dict-shaped membership (server_id -> tier) is also understood
    dict_result = generate_report(_FakeConnector(
        servers, perspectives,
        [{"perspective_id": "p1", "membership": {"srv_a": "LOW", "srv_b": "LOW",
                                                "srv_c": "LOW", "srv_high": "HIGH"}}]))
    assert dict_result["uncovered_servers"] == 0, dict_result
    assert dict_result["coverage_pct"] == 100.0, dict_result

    text = summarize(result)
    assert "uncovered" in text.lower(), text
    print("PASS")


if __name__ == "__main__":
    _self_test()
