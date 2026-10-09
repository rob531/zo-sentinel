# deps: requests
"""verdict_reconciler.py -- RC-1 verdict reconciler + DRY-RUN counter (STAGED).

THE LEVER (RC-1, from the UNKNOWN-rate RCA)
    The promoter stamps ``registry.verdict = 'unknown'`` as the default
    (``auto_promoter.py:39``) and NOTHING re-verdicts a row once axis scores
    land in ``mcp_llm_axis_scores``. ~228k registry rows (~42 points of the
    ~82% UNKNOWN) appear to HAVE axis scores yet still carry
    ``verdict='unknown'``. This module derives the verdict for exactly those
    rows and -- read-only -- counts how many WOULD flip, so the chairman/lane
    can confirm the blast radius before any write.

THE MAPPING IS NOT INVENTED HERE -- IT IS REUSED
    The verdict taxonomy and the axis->verdict mapping are FOREVER-adjacent
    HELD: this module may not define them. It imports and calls the canonical
    mapping already in the repo:

        scoring_tier_persister._compute_tier(server_id, session)

    That is the only daemon that (a) reads ``mcp_llm_axis_scores``, (b) applies
    the PRODUCT_SPEC.md ``§2`` verdict taxonomy
    (TRUSTED_GENERAL / TRUSTED_RESEARCH / ENTERPRISE_CONTROLLED /
    CAUTION_LIMITED / HIGH_RISK_ISOLATED), and (c) upserts into
    ``mcp_server_registry``. Its own docstring cites "PRODUCT_SPEC §2 + §3" and
    it ships a passing hermetic-sqlite self-test. NOTE: that daemon currently
    MISROUTES its taxonomy output into the ``risk_tier`` column -- nothing ever
    writes it into ``registry.verdict``. That misrouting IS RC-1. The reconciler
    reuses the identical mapping and applies its result to the ``verdict``
    column, where it belongs.

WHAT IS HELD
    The actual bulk ``UPDATE mcp_server_registry SET verdict=...`` pass is
    EXECUTION-PLANE + mass-write = HELD. The CLI default is DRY-RUN and writes
    nothing. ``reconcile(..., apply=True)`` exists for the lane to run AFTER the
    chairman confirms the dry-run count; against prod it routes writes through
    ``write_service`` (:8772), never a direct DB UPDATE (ARCHITECTURE: the write
    service is the sole state bus).

ENTRY POINTS
    dry_run(session)                       -> DryRunReport  (READ-ONLY)
    reconcile(session, writer, apply=bool) -> ReconcileResult

CLI
    python verdict_reconciler.py           # DRY-RUN, human-readable (default)
    python verdict_reconciler.py --json    # DRY-RUN, JSON
    python verdict_reconciler.py --apply   # HELD: refuses unless the lane sets
                                           #   ZO_RECONCILE_CONFIRM=1
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry

# Reuse the canonical axis->verdict mapping. We import the function; we do NOT
# reimplement it. If this import breaks, the mapping moved and the reconciler
# must be re-pointed rather than guessed.
from scoring_tier_persister import _compute_tier as _canonical_axis_to_verdict

#: The promoter's default stamp (auto_promoter.py:39). These are the rows RC-1
#: is about. Kept narrow on purpose: this is the exact bucket the ~228k estimate
#: refers to.
UNKNOWN_VERDICT = "unknown"

WRITE_SERVICE = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass
class DryRunReport:
    """READ-ONLY summary. Nothing in producing this writes to any store."""

    candidates_considered: int = 0          # unknown AND >=1 axis-score row
    would_flip: int = 0                     # candidates that derive a real verdict
    would_not_flip: int = 0                 # axis rows exist but mapping abstains
    flip_distribution: Dict[str, int] = field(default_factory=dict)  # derived -> n

    def as_dict(self) -> dict:
        return {
            "candidates_considered": self.candidates_considered,
            "would_flip": self.would_flip,
            "would_not_flip": self.would_not_flip,
            "flip_distribution": self.flip_distribution,
        }


@dataclass
class ReconcileResult:
    dry_run: DryRunReport
    applied: int = 0
    apply_mode: bool = False


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
def candidate_server_ids(session: Session) -> List[str]:
    """server_ids that are RC-1 candidates: verdict='unknown' AND have >=1 row
    in mcp_llm_axis_scores. Portable SQL (sqlite in test, Postgres in prod)."""
    scored = select(McpLlmAxisScore.server_id).distinct()
    rows = session.execute(
        select(McpServerRegistry.server_id).where(
            McpServerRegistry.verdict == UNKNOWN_VERDICT,
            McpServerRegistry.server_id.in_(scored),
        )
    ).all()
    return [r[0] for r in rows]


def derive_verdict(server_id: str, session: Session) -> Optional[str]:
    """Reuse the canonical axis->verdict mapping. Returns a taxonomy verdict or
    None when the mapping abstains (no usable axis rows / no model version)."""
    return _canonical_axis_to_verdict(server_id, session)


def dry_run(session: Session) -> DryRunReport:
    """Count how many unknown-with-axis-scores rows WOULD flip and to what.
    READ-ONLY: this is the artifact that verifies or refutes RC-1's hypothesis."""
    report = DryRunReport()
    dist: Counter = Counter()
    current = {
        sid: v
        for sid, v in session.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.verdict)
        ).all()
    }
    for sid in candidate_server_ids(session):
        report.candidates_considered += 1
        derived = derive_verdict(sid, session)
        if derived is not None and derived != current.get(sid):
            report.would_flip += 1
            dist[derived] += 1
        else:
            report.would_not_flip += 1
    report.flip_distribution = dict(dist)
    return report


def _write_service_writer(server_id: str, verdict: str) -> bool:
    """Prod writer: route the single-row UPDATE through write_service (:8772).
    Never a direct DB UPDATE -- the write service is the sole state bus. Guarded
    with a verdict='unknown' predicate so a concurrent re-verdict is not clobbered.
    """
    import requests  # local import: dry-run path never needs the network

    ts = datetime.now(timezone.utc).isoformat()
    resp = requests.post(
        f"{WRITE_SERVICE}/execute",
        json={
            "sql": (
                "UPDATE mcp_server_registry "
                "SET verdict = :verdict, last_assessed = :ts "
                "WHERE server_id = :sid AND verdict = :unknown"
            ),
            "params": {
                "verdict": verdict,
                "ts": ts,
                "sid": server_id,
                "unknown": UNKNOWN_VERDICT,
            },
            "wait": True,
        },
        timeout=10,
    )
    return resp.status_code < 500


def reconcile(
    session: Session,
    writer: Optional[Callable[[str, str], bool]] = None,
    apply: bool = False,
) -> ReconcileResult:
    """Derive verdicts for RC-1 candidates. With apply=False (default) this is a
    dry-run and writes nothing. With apply=True each would-flip is handed to
    ``writer(server_id, verdict)`` (defaults to the write_service writer).

    apply=True against prod is HELD -- the lane runs it after the chairman
    confirms the dry-run count.
    """
    report = dry_run(session)
    result = ReconcileResult(dry_run=report, apply_mode=apply)
    if not apply:
        return result

    if writer is None:
        writer = _write_service_writer

    current = {
        sid: v
        for sid, v in session.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.verdict)
        ).all()
    }
    for sid in candidate_server_ids(session):
        derived = derive_verdict(sid, session)
        if derived is not None and derived != current.get(sid):
            if writer(sid, derived):
                result.applied += 1
    return result


def session_writer(session: Session) -> Callable[[str, str], bool]:
    """A writer that applies the UPDATE through the given ORM session. Used by
    the hermetic two-pole test; NOT used against prod (prod routes through the
    write service)."""

    def _w(server_id: str, verdict: str) -> bool:
        ts = datetime.now(timezone.utc)
        session.execute(
            update(McpServerRegistry)
            .where(
                McpServerRegistry.server_id == server_id,
                McpServerRegistry.verdict == UNKNOWN_VERDICT,
            )
            .values(verdict=verdict, last_assessed=ts)
        )
        session.commit()
        return True

    return _w


# --------------------------------------------------------------------------- #
# CLI -- DRY-RUN by default
# --------------------------------------------------------------------------- #
def _render(report: DryRunReport) -> str:
    lines = [
        "RC-1 verdict reconciler -- DRY RUN (read-only, nothing written)",
        f"  candidates (verdict='{UNKNOWN_VERDICT}' AND >=1 axis-score row): "
        f"{report.candidates_considered}",
        f"  WOULD FLIP:     {report.would_flip}",
        f"  would not flip: {report.would_not_flip}  (axis rows exist but mapping abstains)",
        "  flip distribution (axis-derived verdict -> count):",
    ]
    if report.flip_distribution:
        for verdict, n in sorted(report.flip_distribution.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {verdict:<24} {n}")
    else:
        lines.append("    (none)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    do_apply = "--apply" in argv

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        if do_apply:
            if os.environ.get("ZO_RECONCILE_CONFIRM") != "1":
                print(
                    "--apply is HELD (EXECUTION-PLANE mass write). Refusing.\n"
                    "The lane runs this only AFTER the chairman confirms the "
                    "dry-run count, with ZO_RECONCILE_CONFIRM=1 set.",
                    file=sys.stderr,
                )
                return 2
            result = reconcile(session, apply=True)
            print(json.dumps({"applied": result.applied,
                              "dry_run": result.dry_run.as_dict()}, indent=2))
            return 0

        report = dry_run(session)
        print(json.dumps(report.as_dict(), indent=2) if as_json else _render(report))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
