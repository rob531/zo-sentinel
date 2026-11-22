# deps: sqlalchemy
"""risk_tier_transition_matrix_report.py -- risk-tier transition matrix report.

Renders the old_tier -> new_tier transition counts used by the 3-tier app
surface (low / medium / high / critical; anything else -> unknown).

Two layers:
  * pure report layer -- build_transition_matrix(rows) and main() are pure
    stdlib (no DB, no network, no file writes); build_transition_matrix does
    no I/O at all.
  * data seam -- load_transition_rows(db) reads the REAL app table
    (mcp_server_registry) through the REAL app session dependency
    (app.db.get_session) and app.models.McpServerRegistry, returning rows in
    the shape the report layer expects.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

TIERS = ("low", "medium", "high", "critical", "unknown")
UNKNOWN = "unknown"

_META_PREV_KEYS = ("previous_verdict", "previous_risk_tier", "prior_verdict")


# ---------------------------------------------------------------- pure layer

def normalize_tier(value: Any) -> str:
    """Normalise any tier value to low/medium/high/critical/unknown."""
    if value is None:
        return UNKNOWN
    text = str(value).strip().lower()
    return text if text in TIERS[:-1] else UNKNOWN


def build_transition_matrix(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the transition matrix from aggregated transition rows.

    rows: dicts with "old_tier" (str|None), "new_tier" (str|None), "count" (int).
    Tiers are normalised to low/medium/high/critical; anything else -> unknown.
    Duplicate (from, to) pairs are summed, first-seen order is preserved.

    Returns {"cells": [{"from": tier, "to": tier, "count": int}],
             "totals": {"from": {tier: int}, "to": {tier: int}},
             "grand_total": int}. Pure: no I/O.
    """
    cells: Dict[tuple, int] = {}
    order: List[tuple] = []
    totals_from: Dict[str, int] = {}
    totals_to: Dict[str, int] = {}
    grand_total = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw_count = row.get("count", 0)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        src = normalize_tier(row.get("old_tier"))
        dst = normalize_tier(row.get("new_tier"))
        key = (src, dst)
        if key not in cells:
            cells[key] = 0
            order.append(key)
        cells[key] += count
        totals_from[src] = totals_from.get(src, 0) + count
        totals_to[dst] = totals_to.get(dst, 0) + count
        grand_total += count

    return {
        "cells": [{"from": src, "to": dst, "count": cells[(src, dst)]} for src, dst in order],
        "totals": {"from": totals_from, "to": totals_to},
        "grand_total": grand_total,
    }


def format_text_report(matrix: Dict[str, Any]) -> str:
    """Render the matrix as a fixed-width text table."""
    label_w = 10
    col_w = 9
    total_w = 8

    cells = {}
    for cell in (matrix or {}).get("cells") or []:
        cells[(cell.get("from"), cell.get("to"))] = cell.get("count", 0)

    header = f"{'old\\new':<{label_w}}"
    for tier in TIERS:
        header += f"{tier:>{col_w}}"
    header += f"{'TOTAL':>{total_w}}"
    lines = ["Risk-tier transition matrix (old_tier -> new_tier)", header]

    totals_from = (matrix or {}).get("totals", {}).get("from", {}) or {}
    totals_to = (matrix or {}).get("totals", {}).get("to", {}) or {}
    grand_total = (matrix or {}).get("grand_total", 0) or 0

    for src in TIERS:
        if src not in totals_from:
            continue
        line = f"{src:<{label_w}}"
        for dst in TIERS:
            line += f"{cells.get((src, dst), 0):>{col_w}}"
        line += f"{totals_from.get(src, 0):>{total_w}}"
        lines.append(line)

    total_line = f"{'TOTAL':<{label_w}}"
    for dst in TIERS:
        total_line += f"{totals_to.get(dst, 0):>{col_w}}"
    total_line += f"{grand_total:>{total_w}}"
    lines.append(total_line)

    return "\n".join(lines)


def main(rows: Optional[List[Dict[str, Any]]] = None) -> int:
    """Render the matrix as a text table. Returns 0."""
    matrix = build_transition_matrix(rows or [])
    print(format_text_report(matrix))
    return 0


# --------------------------------------------------------------- data seam

def _previous_tier(meta: Any) -> Optional[str]:
    """Previous risk tier recorded on a registry row, if any."""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            return None
    if not isinstance(meta, dict):
        return None
    previous = None
    for key in _META_PREV_KEYS:
        if meta.get(key) is not None:
            previous = meta.get(key)
            break
    if isinstance(previous, dict):
        previous = previous.get("risk_tier", previous.get("tier", previous.get("verdict")))
    if previous is None:
        return None
    return str(previous)


def load_transition_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the REAL registry and return aggregated transition rows."""
    rows = db.execute(
        select(McpServerRegistry.server_id,
               McpServerRegistry.risk_tier,
               McpServerRegistry.meta)
    ).all()

    counts: Dict[tuple, int] = {}
    order: List[tuple] = []
    for _sid, risk_tier, meta in rows:
        previous = _previous_tier(meta)
        if previous is None:
            continue
        src = normalize_tier(previous)
        dst = normalize_tier(risk_tier)
        key = (src, dst)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1

    return [{"old_tier": src, "new_tier": dst, "count": counts[(src, dst)]}
            for src, dst in order]


# ------------------------------------------------------------------- self-test

def _self_test() -> int:
    # --- pure layer ---
    assert build_transition_matrix([]) == {
        "cells": [], "totals": {"from": {}, "to": {}}, "grand_total": 0}

    m = build_transition_matrix([
        {"old_tier": "low", "new_tier": "high", "count": 2},
        {"old_tier": None, "new_tier": "low", "count": 1},
    ])
    assert m["cells"] == [{"from": "low", "to": "high", "count": 2},
                          {"from": "unknown", "to": "low", "count": 1}], m
    assert m["grand_total"] == 3, m
    assert m["totals"]["from"]["low"] == 2, m
    assert m["totals"]["to"]["low"] == 1, m

    # case / unknown normalisation + duplicate aggregation
    m2 = build_transition_matrix([
        {"old_tier": "LOW", "new_tier": "Critical", "count": 1},
        {"old_tier": "LOW", "new_tier": "CRITICAL", "count": 2},
        {"old_tier": "unassessed", "new_tier": "medium", "count": 1},
    ])
    assert m2["cells"] == [{"from": "low", "to": "critical", "count": 3},
                           {"from": "unknown", "to": "medium", "count": 1}], m2
    assert m2["grand_total"] == 4, m2

    # invalid counts are ignored
    m3 = build_transition_matrix([
        {"old_tier": "low", "new_tier": "high", "count": "nope"},
        {"old_tier": "low", "new_tier": "high", "count": -2},
        {"old_tier": "low", "new_tier": "high", "count": 1},
    ])
    assert m3["grand_total"] == 1, m3

    # --- text report ---
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([{"old_tier": "low", "new_tier": "high", "count": 2}])
    out = buf.getvalue()
    assert rc == 0, rc
    assert out.splitlines()[0].startswith("Risk-tier transition matrix"), out
    assert "low" in out and "high" in out, out

    # --- data seam: real app models over SQLite ---
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        s.add(McpServerRegistry(server_id="srv1", name="Alpha MCP",
                                url="https://github.com/example/alpha-mcp",
                                registry_source="github", risk_tier="high",
                                meta=json.dumps({"previous_risk_tier": "low"})))
        s.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                                url="https://github.com/example/beta-mcp",
                                registry_source="github", risk_tier="medium",
                                meta=json.dumps({"previous_verdict": "low"})))
        s.add(McpServerRegistry(server_id="srv3", name="Gamma MCP",
                                url="https://github.com/example/gamma-mcp",
                                registry_source="github", risk_tier="critical"))
        s.commit()

        transition_rows = load_transition_rows(s)
        assert transition_rows == [{"old_tier": "low", "new_tier": "high", "count": 1},
                                   {"old_tier": "low", "new_tier": "medium", "count": 1}], \
            transition_rows
        matrix = build_transition_matrix(transition_rows)
        assert matrix["grand_total"] == 2, matrix
        assert matrix["totals"]["from"]["low"] == 2, matrix
        assert matrix["totals"]["to"]["high"] == 1, matrix
        assert "critical" not in matrix["totals"]["to"], matrix
    finally:
        s.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
