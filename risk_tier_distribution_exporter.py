"""risk_tier_distribution_exporter.py -- registry risk-tier distribution report.

Standalone, non-HTTP report generator: turns registry rows into a tier
distribution (+ per-tier registry_source breakdown) and exports them as CSV.

Data comes from the app Postgres through the real app data layer
(app.db.get_session / app.models.McpServerRegistry) -- no inline stubs, no
file-based inputs (CSV here is export-only).

Pure functions (build_tier_distribution / export_report): stdlib only, no DB
writes, no network. `mcp_server_registry.risk_tier` is the ALREADY-PUBLISHED
tier column (post trust-gating), so no trust_gate re-application is needed --
this report does not surface raw model labels.

Build marker: goose module-from-exemplar build (mirrors verdict_breakdown_api.py).
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

# Deterministic tier order (riskiest first); anything else follows, alpha.
TIER_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
UNKNOWN_TIER = "UNKNOWN"
UNKNOWN_SOURCE = "UNKNOWN"


def _norm_tier(value: Optional[str]) -> str:
    t = (value or "").strip().upper()
    return t or UNKNOWN_TIER


def _norm_source(value: Optional[str]) -> str:
    s = (value or "").strip()
    return s or UNKNOWN_SOURCE


def _tier_sort_key(tier: str):
    try:
        return (0, TIER_ORDER.index(tier))
    except ValueError:
        return (1, 0)


def _ordered_tiers(tiers) -> List[str]:
    known = [t for t in tiers if t in TIER_ORDER]
    extra = sorted(t for t in tiers if t not in TIER_ORDER)
    known.sort(key=lambda t: TIER_ORDER.index(t))
    return known + extra


def build_tier_distribution(rows: List[dict]) -> dict:
    """Registry rows -> {"tiers": {tier: count}, "total": int,
    "by_source": {source: {tier: count}}}. Pure: no DB, no network, no writes."""
    tier_counts: Counter = Counter()
    by_source: Dict[str, Counter] = {}

    for row in rows or []:
        tier = _norm_tier(row.get("risk_tier"))
        source = _norm_source(row.get("registry_source"))
        tier_counts[tier] += 1
        by_source.setdefault(source, Counter())[tier] += 1

    tiers = {t: tier_counts[t] for t in _ordered_tiers(tier_counts.keys())}
    sources = {s: {t: c[t] for t in _ordered_tiers(c.keys())}
               for s, c in sorted(by_source.items())}
    return {"tiers": tiers, "total": sum(tier_counts.values()), "by_source": sources}


def export_report(rows: List[dict], out_dir: str) -> List[str]:
    """Write tier_distribution.csv + tier_by_source.csv under out_dir (created if
    absent). Returns the written file paths."""
    dist = build_tier_distribution(rows)
    os.makedirs(out_dir, exist_ok=True)

    dist_path = os.path.join(out_dir, "tier_distribution.csv")
    with open(dist_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "count"])
        for tier, count in dist["tiers"].items():
            w.writerow([tier, count])

    src_path = os.path.join(out_dir, "tier_by_source.csv")
    with open(src_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "tier", "count"])
        for source, tiers in dist["by_source"].items():
            for tier, count in tiers.items():
                w.writerow([source, tier, count])

    return [dist_path, src_path]


def load_registry_rows(db: Session = Depends(get_session)) -> List[dict]:
    """Registry rows -> the row shape build_tier_distribution expects. Read-only."""
    rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier,
               McpServerRegistry.registry_source, McpServerRegistry.last_assessed)
    ).all()
    return [{"server_id": r[0], "risk_tier": r[1], "registry_source": r[2],
             "last_assessed": r[3].isoformat() if r[3] is not None else None}
            for r in rows]


def export_registry_report(db: Session, out_dir: str) -> dict:
    """Read the registry through the app session, then export the distribution."""
    rows = load_registry_rows(db)
    dist = build_tier_distribution(rows)
    paths = export_report(rows, out_dir)
    return {"distribution": dist, "files": paths, "rows": len(rows)}


def distribution_json(rows: List[dict]) -> str:
    """The distribution as a JSON string (report consumers that want JSON)."""
    return json.dumps(build_tier_distribution(rows), sort_keys=True)


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    import tempfile

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="official", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv2", name="Some MCP",
                            url="https://example.org/mcp",
                            registry_source="community", risk_tier="HIGH"))
    s.commit(); s.close()

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    sess = TS()
    db_rows = load_registry_rows(sess)
    sess.close()
    assert len(db_rows) == 2, db_rows
    assert db_rows[0]["server_id"] == "srv1", db_rows
    assert db_rows[0]["last_assessed"] is None, db_rows

    # 5 fake rows: 3 tiers + one blank tier (+ one normal).
    rows = [
        {"server_id": "a", "risk_tier": "CRITICAL", "registry_source": "official",
         "last_assessed": "2026-01-01T00:00:00+00:00"},
        {"server_id": "b", "risk_tier": "HIGH", "registry_source": "official",
         "last_assessed": "2026-01-02T00:00:00+00:00"},
        {"server_id": "c", "risk_tier": "HIGH", "registry_source": "community",
         "last_assessed": None},
        {"server_id": "d", "risk_tier": "", "registry_source": "community",
         "last_assessed": None},
        {"server_id": "e", "risk_tier": None, "registry_source": None,
         "last_assessed": None},
    ]
    dist = build_tier_distribution(rows)
    assert dist["total"] == 5, dist
    assert dist["tiers"]["UNKNOWN"] == 2, dist
    assert dist["tiers"]["HIGH"] == 2, dist
    assert dist["tiers"]["CRITICAL"] == 1, dist
    assert list(dist["tiers"].keys())[:3] == ["CRITICAL", "HIGH", "UNKNOWN"], dist
    assert dist["by_source"]["official"]["HIGH"] == 1, dist
    assert dist["by_source"]["community"]["UNKNOWN"] == 1, dist
    assert dist["by_source"]["UNKNOWN"]["UNKNOWN"] == 1, dist
    assert json.loads(distribution_json(rows))["total"] == 5

    with tempfile.TemporaryDirectory() as td:
        paths = export_report(rows, td)
        assert len(paths) == 2, paths
        for p in paths:
            assert os.path.exists(p), p
            assert os.path.getsize(p) > 0, p
        assert os.path.basename(paths[0]) == "tier_distribution.csv", paths
        assert os.path.basename(paths[1]) == "tier_by_source.csv", paths
        with open(paths[0], encoding="utf-8") as fh:
            head = fh.readline().strip()
        assert head == "tier,count", head
        with open(paths[1], encoding="utf-8") as fh:
            head2 = fh.readline().strip()
        assert head2 == "source,tier,count", head2

    print("PASS")
