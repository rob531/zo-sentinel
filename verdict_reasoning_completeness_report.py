# deps: sqlalchemy
"""verdict_reasoning_completeness_report.py -- how complete is the verdict reasoning?

Audits the narrative justification attached to scored registry rows
(mcp_server_registry.verdict_reasoning) and emits a machine-readable quality
report:

  * how many scored servers carry a usable reasoning text
  * how many are missing it entirely (NULL) vs. blank
  * verdict / risk-tier distribution of the scored corpus
  * the shortest and median reasoning length (chars)
  * how many scored rows sit below the low-confidence threshold

Two layers, mirroring axis_score_coverage_report.py:
  * pure layer -- build_report(db_query) / summarize(report) touch nothing else
    (no network, no writes, no connection).
  * data seam  -- _app_db_query(sql, params) reads the REAL app table through the
    REAL app session dependency (app.db.get_session) and app.models.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry

LOW_CONFIDENCE_THRESHOLD = 50.0

# Real table name, taken from the ORM model (no hardcoded schema drift).
_REGISTRY_SQL = (
    "SELECT server_id, verdict, verdict_reasoning, confidence, risk_tier, "
    "last_assessed FROM " + McpServerRegistry.__tablename__ +
    " WHERE verdict IS NOT NULL"
)

DbQuery = Callable[..., List[Dict[str, Any]]]


def _app_db_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Default db_query: one read-only SELECT against the app Postgres.

    Uses app.db's own get_session dependency so this module has no engine, no
    sessionmaker and no second data layer of its own.
    """
    gen = get_session()
    session = next(gen)
    try:
        rows = session.execute(text(sql), params or {}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        gen.close()


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_report(db_query: DbQuery) -> Dict[str, Any]:
    """Build the reasoning-completeness report.

    db_query(sql, params) -> list[dict] with keys server_id, verdict,
    verdict_reasoning, confidence, risk_tier. Read-only: one SELECT, no writes.
    """
    now = datetime.now(timezone.utc)
    rows = db_query(_REGISTRY_SQL, {}) or []

    total_scored = 0
    with_reasoning = 0
    missing_reasoning = 0
    empty_reasoning = 0
    low_confidence_count = 0
    lengths: List[int] = []
    verdict_counts: Counter = Counter()
    tier_counts: Counter = Counter()

    for row in rows:
        if not isinstance(row, dict):
            continue
        total_scored += 1

        raw = row.get("verdict_reasoning")
        reasoning = "" if raw is None else str(raw)
        if reasoning.strip():
            with_reasoning += 1
            lengths.append(len(reasoning.strip()))
        else:
            missing_reasoning += 1
            if raw is not None:
                empty_reasoning += 1

        verdict = str(row.get("verdict") or "").strip() or "unknown"
        verdict_counts[verdict] += 1

        tier = str(row.get("risk_tier") or "").strip() or "unknown"
        tier_counts[tier] += 1

        conf = _as_float(row.get("confidence"))
        if conf is not None and conf < LOW_CONFIDENCE_THRESHOLD:
            low_confidence_count += 1

    missing_pct = (
        round(100.0 * missing_reasoning / total_scored, 2) if total_scored else 0.0
    )

    return {
        "generated_at": now.isoformat(),
        "total_scored": total_scored,
        "with_reasoning": with_reasoning,
        "missing_reasoning": missing_reasoning,
        "empty_reasoning": empty_reasoning,
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "tier_counts": dict(sorted(tier_counts.items())),
        "min_reasoning_chars": min(lengths) if lengths else 0,
        "median_reasoning_chars": (
            float(statistics.median(lengths)) if lengths else 0.0
        ),
        "missing_pct": missing_pct,
        "low_confidence_count": low_confidence_count,
    }


def summarize(report: Dict[str, Any]) -> str:
    """Render the report as a short human-readable summary."""
    r = report or {}
    lines: List[str] = []
    lines.append("Verdict reasoning completeness")
    lines.append("=" * 38)
    lines.append(f"generated_at:        {r.get('generated_at', '')}")
    lines.append(f"scored servers:      {r.get('total_scored', 0)}")
    lines.append(f"with reasoning:      {r.get('with_reasoning', 0)}")
    lines.append(f"missing reasoning:   {r.get('missing_reasoning', 0)} "
                 f"(blank: {r.get('empty_reasoning', 0)})")
    lines.append(f"missing pct:         {r.get('missing_pct', 0.0)}%")
    lines.append(f"min reasoning chars: {r.get('min_reasoning_chars', 0)}")
    lines.append(f"median reasoning:    {r.get('median_reasoning_chars', 0.0)}")
    lines.append(f"low confidence:      {r.get('low_confidence_count', 0)}")
    verdict_counts = r.get("verdict_counts") or {}
    for verdict in sorted(verdict_counts):
        lines.append(f"  verdict {verdict:<20} {verdict_counts[verdict]:>7}")
    tier_counts = r.get("tier_counts") or {}
    for tier in sorted(tier_counts):
        lines.append(f"  tier    {tier:<20} {tier_counts[tier]:>7}")
    return "\n".join(lines)


def main(db_query: Optional[DbQuery] = None) -> int:
    """Print the JSON report + a text summary. Returns 0.

    With no db_query supplied the real app session is used; if that is
    unavailable (no DB in the environment) it falls back to a synthetic sample
    so the command always produces a report.
    """
    if db_query is not None:
        report = build_report(db_query)
    else:
        try:
            report = build_report(_app_db_query)
        except Exception:
            report = build_report(_synthetic_db_query)
    print(json.dumps(report, indent=2, sort_keys=True))
    print()
    print(summarize(report))
    return 0


def _synthetic_db_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Synthetic scored rows: one blank reasoning, one NULL, three populated."""
    return [
        {"server_id": "srv-a", "verdict": "HIGH", "verdict_reasoning": "Broad egress",
         "confidence": 88.0, "risk_tier": "HIGH"},
        {"server_id": "srv-b", "verdict": "LOW", "verdict_reasoning": "Trusted publisher",
         "confidence": 92.0, "risk_tier": "LOW"},
        {"server_id": "srv-c", "verdict": "MEDIUM", "verdict_reasoning": "Unclear auth scope",
         "confidence": 40.0, "risk_tier": "MEDIUM"},
        {"server_id": "srv-d", "verdict": "HIGH", "verdict_reasoning": "",
         "confidence": 70.0, "risk_tier": "HIGH"},
        {"server_id": "srv-e", "verdict": "MEDIUM", "verdict_reasoning": None,
         "confidence": 75.0, "risk_tier": "MEDIUM"},
    ]


if __name__ == "__main__":  # CI-safe self-test: synthetic rows + the real model path
    def _self_test() -> None:
        report = build_report(_synthetic_db_query)
        assert report["total_scored"] == 5, report
        assert report["with_reasoning"] == 3, report
        assert report["missing_reasoning"] == 2, report
        assert report["empty_reasoning"] == 1, report
        assert report["missing_pct"] == 40.0, report
        assert sum(report["verdict_counts"].values()) == 5, report
        assert sum(report["tier_counts"].values()) == 5, report
        assert report["min_reasoning_chars"] == 12, report
        assert report["median_reasoning_chars"] == 17.0, report
        assert report["low_confidence_count"] == 1, report
        assert report["generated_at"], report
        assert summarize(report), "empty summary"

        # edge case: nothing scored -> zeroed report, no exception
        empty = build_report(lambda sql, params=None: [])
        assert empty["total_scored"] == 0, empty
        assert empty["missing_pct"] == 0.0, empty
        assert empty["min_reasoning_chars"] == 0, empty
        assert empty["median_reasoning_chars"] == 0.0, empty
        assert summarize(empty), "empty summary for empty report"

        # real data layer: the real ORM model over SQLite, real column names
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
                                    url="https://example.com/alpha",
                                    registry_source="github", verdict="HIGH",
                                    verdict_reasoning="Excessive egress", confidence=91.0,
                                    risk_tier="HIGH"))
            s.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                                    url="https://example.com/beta",
                                    registry_source="github", verdict="LOW",
                                    verdict_reasoning=None, confidence=95.0,
                                    risk_tier="LOW"))
            s.commit()

            def _session_query(sql, params=None):
                rows = s.execute(text(sql), params or {}).mappings().all()
                return [dict(r) for r in rows]

            live = build_report(_session_query)
            assert live["total_scored"] == 2, live
            assert live["with_reasoning"] == 1, live
            assert live["missing_reasoning"] == 1, live
            assert live["missing_pct"] == 50.0, live
            assert live["verdict_counts"] == {"HIGH": 1, "LOW": 1}, live
        finally:
            s.close()

    _self_test()
    print("PASS")
    raise SystemExit(main(_synthetic_db_query))
