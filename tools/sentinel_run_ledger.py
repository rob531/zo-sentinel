#!/usr/bin/env python3
"""sentinel_run_ledger.py -- did every scheduled sentinel run actually leave a record?

prod-drift-sentinel runs on a 3h cron and its ONLY durable record is
``prod_deploy_state.json`` (written near the END of a run) plus the verdict
artifacts that ``verify_candidate.ps1`` rescues into ``_deploy_evidence/``
(written in the MIDDLE of a run).

That ordering has a hole, and the hole was observed live on 2026-07-29:

    _deploy_evidence/verdict_7fc39201_20260729T075153Z.json   checked_utc 07:51:53Z
    prod_deploy_state.json                                    last_check_utc 05:01:00Z

An 07:47Z slot ran the full 8-gate verification -- the expensive, load-bearing
part -- and then ended without writing state. Nothing noticed. The chairman's
record says the last check was at 05:01Z, and by that record the 07:47 slot
simply never happened. A run that does the work and leaves no record is
indistinguishable, afterwards, from a run that never fired.

This tool closes that by reconciling three independent facts:

  1. the expected cron slots in a window,
  2. the receipts the run itself wrote (``run_receipts`` in the state file),
  3. the evidence artifacts on disk (``checked_utc`` INSIDE each json).

  exit 0  CLEAN   -- every expected slot is accounted for, no orphan evidence.
  exit 1  GAP     -- a slot left no trace, or evidence exists that state never recorded.
  exit 2  ERROR   -- could not establish the answer. Never treat as CLEAN.
                     (A probe that cannot evaluate is not a green.)

Timestamps are read from FILE CONTENT (``checked_utc``), falling back to the
NAME timestamp -- never from mtime. mtimes do not track run age on this box
(FU-025) and they give the right answer often enough to hide that they are the
wrong method.

Usage:
    python tools/sentinel_run_ledger.py [--state PATH] [--evidence-dir PATH]
                                        [--now ISO8601] [--window-hours 24]
                                        [--tolerance-min 25] [--json]
    python tools/sentinel_run_ledger.py --record-receipt [--now ISO8601]
        # append an idempotent receipt for THIS run, at its START, so a run that
        # dies later still proves it fired.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_STATE = Path(r"D:\zo\Zocomputer Agents\prod_deploy_state.json")
DEFAULT_EVIDENCE = Path(r"D:\zo\Zocomputer Agents\_deploy_evidence")

# prod-drift-sentinel cron: "47 */3 * * *" in LOCAL time (UTC-4 on this box),
# which lands on :47 past 01,04,07,10,13,16,19,22 UTC. Expressed in UTC here
# because every other timestamp in this system is UTC.
SLOT_MINUTE = 47
SLOT_EVERY_HOURS = 3
SLOT_UTC_ANCHOR_HOUR = 1  # 01:47Z, then every SLOT_EVERY_HOURS

NAME_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")

EXIT_CLEAN, EXIT_GAP, EXIT_ERROR = 0, 1, 2


class LedgerError(Exception):
    """Raised when the question cannot be answered. Never becomes a verdict."""


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 instant, tolerating a trailing 'Z'."""
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"not a timestamp: {value!r}")
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LedgerError(f"unparseable timestamp {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def name_ts(path: Path):
    """Timestamp from the FILE NAME. Used only as a fallback -- never mtime."""
    match = NAME_TS_RE.search(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )


def read_state(state_path: Path) -> dict:
    if not state_path.exists():
        raise LedgerError(f"state file does not exist: {state_path}")
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f"state file unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError("state file is not a JSON object")
    return data


def collect_evidence(evidence_dir: Path) -> list:
    """Every verdict artifact with the instant it was CHECKED (from content)."""
    if not evidence_dir.exists():
        raise LedgerError(f"evidence dir does not exist: {evidence_dir}")
    out = []
    for path in sorted(evidence_dir.glob("verdict_*.json")):
        if path.name == "verdict_latest.json":
            continue  # a POINTER at the newest artifact, not a run of its own
        checked = None
        source = "checked_utc"
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(blob, dict) and blob.get("checked_utc"):
                checked = parse_iso(blob["checked_utc"])
        except (OSError, json.JSONDecodeError, LedgerError):
            checked = None
        if checked is None:
            checked = name_ts(path)
            source = "name_ts"
        if checked is None:
            # Neither content nor name can date it. Do not guess from mtime.
            out.append({"path": str(path), "checked_utc": None, "source": "UNDATABLE"})
            continue
        out.append({"path": str(path), "checked_utc": checked, "source": source})
    return out


def expected_slots(now: datetime, window_hours: int) -> list:
    """Cron slots that have already come due inside the window, newest last."""
    if window_hours <= 0:
        raise LedgerError("window-hours must be positive")
    start = now - timedelta(hours=window_hours)
    slots = []
    cursor = (start - timedelta(days=1)).replace(
        hour=SLOT_UTC_ANCHOR_HOUR, minute=SLOT_MINUTE, second=0, microsecond=0
    )
    while cursor <= now:
        if cursor >= start:
            slots.append(cursor)
        cursor += timedelta(hours=SLOT_EVERY_HOURS)
    return slots


def reconcile(
    state: dict,
    evidence: list,
    now: datetime,
    window_hours: int,
    tolerance_min: int,
) -> dict:
    """Three independent facts in, one machine-checkable report out."""
    last_check_raw = state.get("last_check_utc")
    if not last_check_raw:
        raise LedgerError("state file has no last_check_utc")
    last_check = parse_iso(last_check_raw)

    receipts = []
    for item in state.get("run_receipts") or []:
        receipts.append(parse_iso(item))

    undatable = [e["path"] for e in evidence if e["checked_utc"] is None]
    dated = [e for e in evidence if e["checked_utc"] is not None]

    # ORPHAN EVIDENCE: the verification ran, the record never landed.
    orphans = [
        {"path": e["path"], "checked_utc": fmt(e["checked_utc"]), "source": e["source"]}
        for e in dated
        if e["checked_utc"] > last_check
        and not any(
            abs((e["checked_utc"] - r).total_seconds()) <= tolerance_min * 60
            for r in receipts
        )
    ]

    tol = timedelta(minutes=tolerance_min)
    traces = [e["checked_utc"] for e in dated] + receipts + [last_check]

    # Only ONE last_check_utc survives in the state file, so a run that finished
    # cleanly two days ago has had its record overwritten by every run since.
    # Before receipts began accruing, "no trace" therefore cannot distinguish a
    # slot that never fired from one whose record was overwritten -- and a probe
    # that cannot evaluate is not a red. Those slots are reported as UNATTESTED
    # (advisory) and only slots at or after the first receipt can be MISSED.
    attest_from = min(receipts) if receipts else None
    missed = []
    unattested = []
    for slot in expected_slots(now, window_hours):
        if now - slot < tol:
            continue  # still in flight; not yet owed a trace
        if any(abs((t - slot).total_seconds()) <= tol.total_seconds() for t in traces):
            continue
        if attest_from is not None and slot >= attest_from:
            missed.append(fmt(slot))
        else:
            unattested.append(fmt(slot))

    return {
        "now_utc": fmt(now),
        "last_check_utc": fmt(last_check),
        "window_hours": window_hours,
        "tolerance_min": tolerance_min,
        "receipts": [fmt(r) for r in receipts],
        "attested_from": fmt(attest_from) if attest_from else None,
        "evidence_count": len(evidence),
        "undatable_evidence": undatable,
        "orphan_evidence": orphans,
        "missed_slots": missed,
        "unattested_slots": unattested,
        "clean": not orphans and not missed and not undatable,
    }


def record_receipt(state_path: Path, now: datetime) -> dict:
    """Append an idempotent receipt. Safe to call twice in one run."""
    state = read_state(state_path)
    receipts = list(state.get("run_receipts") or [])
    stamp = fmt(now)
    added = stamp not in receipts
    if added:
        receipts.append(stamp)
    # Keep the tail bounded; this is provenance, not an archive.
    state["run_receipts"] = sorted(set(receipts))[-64:]
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)
    return {"receipt": stamp, "added": added, "count": len(state["run_receipts"])}


def render(report: dict) -> str:
    lines = [
        f"now         : {report['now_utc']}",
        f"last_check  : {report['last_check_utc']}",
        f"receipts    : {len(report['receipts'])}",
        f"evidence    : {report['evidence_count']} artifact(s)",
        "",
    ]
    if report["orphan_evidence"]:
        lines.append("ORPHAN EVIDENCE -- verification ran, state never recorded it:")
        for orphan in report["orphan_evidence"]:
            lines.append(f"  {orphan['checked_utc']}  {orphan['path']}")
        lines.append("")
    if report["missed_slots"]:
        lines.append("MISSED SLOTS -- cron came due and left no trace at all:")
        for slot in report["missed_slots"]:
            lines.append(f"  {slot}")
        lines.append("")
    if report["unattested_slots"]:
        lines.append(
            "unattested slots (ADVISORY -- predate receipts, so absence of a "
            "trace is not evidence of absence):"
        )
        for slot in report["unattested_slots"]:
            lines.append(f"  {slot}")
        lines.append("")
    if report["undatable_evidence"]:
        lines.append("UNDATABLE EVIDENCE -- no checked_utc and no name timestamp:")
        for path in report["undatable_evidence"]:
            lines.append(f"  {path}")
        lines.append("")
    lines.append("VERDICT: CLEAN" if report["clean"] else "VERDICT: GAP")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--now", default=None, help="ISO instant; defaults to real now")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--tolerance-min", type=int, default=25)
    parser.add_argument("--record-receipt", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
        if args.record_receipt:
            result = record_receipt(args.state, now)
            print(
                json.dumps(result)
                if args.json
                else f"receipt {result['receipt']} added={result['added']}"
            )
            return EXIT_CLEAN
        report = reconcile(
            read_state(args.state),
            collect_evidence(args.evidence_dir),
            now,
            args.window_hours,
            args.tolerance_min,
        )
    except LedgerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(json.dumps(report, indent=2) if args.json else render(report))
    return EXIT_CLEAN if report["clean"] else EXIT_GAP


if __name__ == "__main__":
    sys.exit(main())
