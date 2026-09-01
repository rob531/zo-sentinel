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


def _commit_replace(tmp, dest):
    """Commit `tmp` onto `dest` through FU-212's proven fallback.

    WHY (measured 2026-09-01, prod-drift-sentinel 04:47Z). `os.replace` is
    MoveFileEx(REPLACE_EXISTING) and Windows refuses it with WinError 5 when the
    DESTINATION carries a mapped section -- while `open(dest,"r+b")` and a plain
    `os.rename(dest, other)` both still succeed. FU-212 measured this on
    FOLLOWUPS.md in July and wired the rename-swap cure into
    ``tools/fu/fu_lock.py``. It was wired into exactly ONE call site. This writer
    was not one of them, so the mandated run receipt (FU-164) failed 12/12
    attempts over ~40s and step 0 of the lane could not complete.

    A cure wired into one door of many reads as a cure (FU-343). This is another
    door, not another cure -- the algorithm is fu_lock's, unchanged.

    Imported BY FILE PATH with ``sys.modules`` seeded first: this repo has more
    than one copy of the fu_* modules on disk and plain ``import`` picks by
    sys.path order rather than by the tree you are running from.
    """
    import importlib.util
    import sys as _sys
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fu", "fu_lock.py")
    spec = importlib.util.spec_from_file_location("_zo_fu_lock_for_commit", p)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["_zo_fu_lock_for_commit"] = mod   # before exec_module, or dataclasses dies
    spec.loader.exec_module(mod)
    return mod.replace_with_fallback(str(tmp), str(dest))

DEFAULT_STATE = Path(r"D:\zo\Zocomputer Agents\prod_deploy_state.json")
DEFAULT_EVIDENCE = Path(r"D:\zo\Zocomputer Agents\_deploy_evidence")

# prod-drift-sentinel cron: "47 */3 * * *" in LOCAL time (UTC-4 on this box),
# which lands on :47 past 01,04,07,10,13,16,19,22 UTC. Expressed in UTC here
# because every other timestamp in this system is UTC.
# THE SLOT GRID IS A CLAIM ABOUT A SCHEDULE THAT LIVES SOMEWHERE ELSE.
# The authority is the scheduled task's own cron, readable with
# `list_scheduled_tasks` -> taskId `prod-drift-sentinel`. As of 2026-07-30 that
# is `15 */3 * * *` in LOCAL time (America/New_York, UTC-4 in summer), i.e.
# 01:15Z, 04:15Z, 07:15Z ... plus a few minutes of dispatch jitter.
#
# These constants said minute 47 until 2026-07-30, and they were RIGHT until
# that morning, when the task was rescheduled to :15 to clear two long runners.
# Nothing connected the constant to the cron, so nothing flagged it when the
# cron moved -- the same shape as FU-202's hardcoded worktree default. Every
# slot was then ~32 minutes off the grid, which under the old nominal-phase
# tolerance of 25 minutes would have reported EVERY FUTURE SLOT AS MISSED.
#
# Nearest-slot attestation (see reconcile) absorbs a phase error of up to half a
# cadence, so these two facts are complementary rather than redundant: the
# corrected grid keeps NEW receipts near their slot, and nearest-slot keeps the
# OLD :47-era receipts still inside the window attested. If you find them
# disagreeing with the live cron again, fix the constant AND ask why a value
# that must track an external schedule is still a literal.
# --- 2026-07-31: THE CADENCE WAS CUT, AND THIS CONSTANT WENT STALE A SECOND TIME.
# The task was reduced from 8 slots/day to 4 (`list_scheduled_tasks` ->
# `cronExpression: "45 0,6,15,20 * * *"`, `jitterSeconds: 97`) after FU-207, in
# which one 17.5h-suspended run starved five consecutive slots.
#
# THE CRON IS EVALUATED IN LOCAL TIME. FU-210 concluded UTC, and was wrong.
#
# FU-210's evidence was `nextRunAt: 2026-08-01T00:46:37Z` == 00:45 + the 97s
# jitter, read as proof that hour 0 of the cron means 00:00Z. It is not proof of
# anything: local 20:45 (the last slot of the local grid) IS 00:45Z. Both
# hypotheses predict that timestamp identically, so the one field checked was the
# single field in the day that cannot discriminate between them. A corroboration
# that both hypotheses predict is not corroboration -- it is a coincidence that
# reads like one, which is worse, because it closes the question.
#
# THE DISCRIMINATING READS, on records that differ under the two hypotheses:
#   1. THIS TASK, PRE-CUT. cron `15 */3 * * *`, `nextRunAt 2026-07-31T22:16:37Z`
#      = 22:15Z + 97s. Under UTC the grid is 00:15,03:15..21:15Z and 22:15Z is
#      NOT ON IT AT ALL. Under local (UTC-4) 22:15Z is 18:15 local, and 18 is on
#      `*/3`. Only one hypothesis can even produce the observed slot.
#   2. A DIFFERENT TASK, so the answer cannot depend on this one's quirks.
#      `mcplookup-nightly-db-backup`: cron `0 3 * * *`, jitter 546s,
#      `nextRunAt 2026-08-01T07:09:06Z` = 07:00:00Z + 546s. Under UTC that record
#      would read 03:09:06Z. It is four hours out -- exactly this box's offset.
# Both were read from the same `list_scheduled_tasks` payload as the cron itself.
#
# The cost of the error was not cosmetic: three of the four daily slots would
# have been graded at instants when no run can occur (06:45Z/15:45Z/20:45Z are
# 02:45/11:45/16:45 local), so every one would go MISSED forever -- and MISSED is
# an email condition. FU-210 set out to stop the grid lying and, uncaught, would
# have converted it from stale to permanently red. A guard that can only go red
# is as broken as one that can only go green, and it is likelier to be silenced.
#
# LOCAL SLOTS 00:45, 06:45, 15:45, 20:45 (America/New_York) -> UTC below.
# These are UTC-4 conversions and are therefore WRONG BY AN HOUR after the DST
# change on 2026-11-01; see `test_grid_is_the_local_cron_converted` for the
# derivation that will fail loudly when it does, rather than drifting quietly.
#
# WHY THERE ARE TWO GRIDS. A 24h window straddling the cut contains slots from
# BOTH cadences. Collapsing them to one grid would either invent phantom slots
# before the cut or erase the five real ones FU-207 is about. GRID_CUT_UTC is set
# between the last legacy slot that actually came due (16:15Z) and the first
# observed post-cut receipt (19:21:41Z); no slot of either grid falls in that gap,
# so the boundary cannot silently add or drop one.
SLOT_LOCAL_HHMM = ((0, 45), (6, 45), (15, 45), (20, 45))
SLOT_TZ = "America/New_York"
SLOT_UTC_HHMM = ((0, 45), (4, 45), (10, 45), (19, 45))
GRID_CUT_UTC = "2026-07-31T18:00:00Z"

# The pre-cut grid, kept so receipts and misses from before the cut are still
# judged against the schedule that was actually in force when they happened.
LEGACY_SLOT_UTC_HHMM = tuple((h, 15) for h in range(1, 24, 3))

# Retained for compatibility with anything importing them; the grid above is the
# authority. Do NOT reintroduce a computation that depends on these.
SLOT_MINUTE = 45
SLOT_EVERY_HOURS = 3
SLOT_UTC_ANCHOR_HOUR = 0

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


#: The lane this ledger speaks for. The evidence directory is SHARED by every
#: lane that runs ops/host/verify_candidate.ps1, so "an artifact exists" and
#: "THIS lane produced an artifact" are different facts and were being conflated.
THIS_LANE = "prod-drift"


def lane_of(path: Path):
    """Which lane produced this artifact, or None if it cannot be attributed.

    None is NOT "mine" and not "foreign" -- it is UNKNOWN, and unknown is not
    zero (R6). Artifacts written before the stamp existed have no field and keep
    the OLD behaviour deliberately: retro-attributing them would be a guess, and
    a guess that silences an alarm is worse than an alarm that names its own
    uncertainty.
    """
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(blob, dict):
        return None
    lane = blob.get("produced_by_lane")
    if not isinstance(lane, str) or not lane:
        return None
    # "unattributed" is the STAMPER'S OWN WORD FOR UNKNOWN, and it must land in
    # the same bucket as an absent field. verify_candidate.ps1 writes it when it
    # can resolve neither $env:ZO_LANE nor a `\_lanes\<name>` component in
    # $PSScriptRoot -- which is exactly what happens when the script is invoked
    # from the SHARED checkout, the path this repo's own runbook prints.
    #
    # Measured 2026-08-07T19:49:42Z: prod-drift ran verify_candidate.ps1 from
    # D:\zo\zo-sentinel\zo-sentinel\ops\host\, its own verdict artifact was
    # stamped "unattributed", and --window-hours 24 reported it as
    #   foreign evidence (ADVISORY, excluded from the orphan test -- another
    #   lane's dry-run in the shared evidence dir)
    # A string meaning "I do not know who wrote this" was read as "somebody else
    # definitely wrote this", which is the one reading that REMOVES it from the
    # orphan test. The guard did not go red. It went QUIET, under a CLEAN verdict.
    #
    # That inverts the rule this function's own docstring states four lines up:
    # unknown is not zero (R6). Absence was handled correctly; the sentinel VALUE
    # that means the same thing was not.
    if lane == "unattributed":
        return None
    return lane


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
        out.append(
            {
                "path": str(path),
                "checked_utc": checked,
                "source": source,
                "produced_by_lane": lane_of(path),
            }
        )
    return out


#: Set by the most recent resolve_post_cut_grid() call so render() can publish
#: the BASIS of the grid alongside the verdict it produced (R5).
GRID_BASIS = "not yet resolved"


def resolve_post_cut_grid(on_date=None):
    """The post-cut grid, FETCHED from the scheduler mirror when possible.

    FU-213. `SLOT_UTC_HHMM` below is correct today because FU-211 retyped it
    correctly; it was also "correct today" after FU-205 and after FU-210, and
    was wrong within a day both times. This asks the mirror -- written from the
    live `list_scheduled_tasks` payload by the daily follow-up-triage run --
    and falls back to the literal, ALWAYS recording which one it used.

    A disagreement is reported, never silently resolved: the mirror wins
    (it is the only party that has actually seen the scheduler) but the literal
    it contradicts is named in the basis, because a grid that changes under a
    reader without saying so is the whole defect being fixed.
    """
    global GRID_BASIS
    try:
        from tools import scheduler_mirror_read as smr
    except ImportError:
        try:
            import scheduler_mirror_read as smr        # flat sys.path
        except ImportError:
            GRID_BASIS = "literal (mirror reader unavailable)"
            return SLOT_UTC_HHMM

    slots, note = smr.utc_slots_for("prod-drift-sentinel", on_date=on_date)
    if slots is None:
        GRID_BASIS = "literal SLOT_UTC_HHMM -- %s" % note
        return SLOT_UTC_HHMM
    if tuple(slots) != tuple(SLOT_UTC_HHMM):
        GRID_BASIS = (
            "MIRROR, and it DISAGREES with the literal in this file: "
            "mirror=%s literal=%s (%s). The mirror wins -- it is the only "
            "party that has read the scheduler -- but fix the literal."
            % (" ".join("%02d:%02d" % s for s in slots),
               " ".join("%02d:%02d" % s for s in SLOT_UTC_HHMM), note))
        return tuple(slots)
    GRID_BASIS = "mirror, agrees with the literal (%s)" % note
    return tuple(slots)


def expected_slots(now: datetime, window_hours: int) -> list:
    """Cron slots that have already come due inside the window, newest last."""
    if window_hours <= 0:
        raise LedgerError("window-hours must be positive")
    start = now - timedelta(hours=window_hours)
    cut = parse_iso(GRID_CUT_UTC)
    post_cut = resolve_post_cut_grid(now.date())
    slots = []
    day = (start - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    last = now + timedelta(days=1)
    while day <= last:
        # UNION, not a per-day choice: a per-day choice cannot emit a slot that
        # exists only under the OTHER cadence, and the cut-over day needs both.
        for hh, mm in sorted(set(LEGACY_SLOT_UTC_HHMM) | set(post_cut)):
            slot = day.replace(hour=hh, minute=mm)
            # Each slot is judged against the cadence in force AT THAT SLOT, not
            # the one in force at the day's start -- otherwise the cut-over day
            # reports whichever grid the loop happened to pick first.
            in_force = LEGACY_SLOT_UTC_HHMM if slot < cut else post_cut
            if (hh, mm) not in in_force:
                continue
            if start <= slot <= now:
                slots.append(slot)
        day += timedelta(days=1)
    return sorted(set(slots))


def grid_phase_report(receipts: list, now: datetime, window_hours: int) -> list:
    """How far is each in-window receipt from its NEAREST declared slot?

    This is the tool doubting its own grid. A slot list is a claim about a
    schedule that lives somewhere else, and this constant has now gone stale
    twice (:47 -> :15 on 2026-07-30, :15 -> the 4-slot cadence on 2026-07-31).
    Both times the symptom was identical to a real outage: a list of MISSED
    SLOTS. The two causes are distinguishable by ONE measurement -- if the task
    genuinely did not run, there is no receipt near the slot; if the GRID is
    wrong, the receipts are all there and all sitting at the same wrong offset.
    Printing the offsets puts that distinction in front of the reader instead of
    leaving it to an audit.

    Returns [(receipt, nearest_slot, delta_minutes)], newest last.
    """
    slots = expected_slots(now, window_hours)
    start = now - timedelta(hours=window_hours)
    out = []
    for r in sorted(receipts):
        if r < start or not slots:
            continue
        nearest = min(slots, key=lambda s: abs((r - s).total_seconds()))
        out.append((r, nearest, (r - nearest).total_seconds() / 60.0))
    return out


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

    # FOREIGN EVIDENCE: produced by a DIFFERENT lane out of the SHARED evidence
    # directory. Measured 2026-08-02: a sibling lane dry-ran verify_candidate.ps1
    # at 18:15Z on 9d365abd, and prod-drift's ledger reported it as ORPHAN
    # EVIDENCE -- "verification ran, state never recorded it" -- on a run whose
    # own receipts (00:47/04:46/10:47/19:47Z) show no missed slot at all. The
    # alarm was about a lane that did nothing wrong, and a HARD signal that fires
    # every time ANY sibling verifies is one that can never clear.
    # Excluded from the orphan test; reported anyway on its own ADVISORY line,
    # because an exclusion nobody can see is indistinguishable from a check that
    # stopped running.
    foreign = [
        {
            "path": e["path"],
            "checked_utc": fmt(e["checked_utc"]),
            "lane": e.get("produced_by_lane"),
        }
        for e in dated
        if e.get("produced_by_lane") not in (None, THIS_LANE)
    ]
    foreign_paths = {f["path"] for f in foreign}

    # ORPHAN EVIDENCE: the verification ran, the record never landed.
    orphans = [
        {"path": e["path"], "checked_utc": fmt(e["checked_utc"]), "source": e["source"]}
        for e in dated
        if e["checked_utc"] > last_check
        and e["path"] not in foreign_paths
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
    # A slot is COVERED BY THE RUN NEAREST TO IT, not only by a run that started
    # within `tolerance_min` of the slot's nominal minute. The scheduler does not
    # fire on an exact phase: on 2026-07-30 the 16:47 slot's run started at
    # 16:17:01 -- thirty minutes early, five minutes outside the tolerance -- and
    # then ran for three hours, so that slot was continuously covered. The old
    # test reported it as "cron came due and left no trace at all", which was
    # false in both clauses: cron had already come, and the trace was sitting in
    # the receipts list. Over that same 24h there were NINE receipts against
    # EIGHT expected slots, so no coverage was lost by any counting.
    #
    # This does NOT weaken the guard, and the direction matters: it is more
    # tolerant of PHASE, not of ABSENCE. A slot with no run within half a cadence
    # still has no run nearest it and is still MISSED -- see the negative control
    # in tests. What it removes is a hard signal that fires when nothing is
    # wrong, which is the failure mode that teaches a reader to stop believing
    # the signal. A MISSED SLOT is an email condition; it must mean something.
    half_cadence = timedelta(hours=SLOT_EVERY_HOURS) / 2
    attest = max(tol, half_cadence)

    attest_from = min(receipts) if receipts else None
    missed = []
    unattested = []
    for slot in expected_slots(now, window_hours):
        if now - slot < attest:
            continue  # still in flight; not yet owed a trace
        if any(abs((t - slot).total_seconds()) < attest.total_seconds() for t in traces):
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
        "foreign_evidence": foreign,
        "grid_phase": [
            (r.strftime("%Y-%m-%dT%H:%M:%SZ"),
             sl.strftime("%Y-%m-%dT%H:%M:%SZ"), d)
            for r, sl, d in grid_phase_report(receipts, now, window_hours)
        ],
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
    _commit_replace(tmp, state_path)
    return {"receipt": stamp, "added": added, "count": len(state["run_receipts"])}


def render(report: dict) -> str:
    lines = [
        f"now         : {report['now_utc']}",
        f"last_check  : {report['last_check_utc']}",
        f"receipts    : {len(report['receipts'])}",
        f"evidence    : {report['evidence_count']} artifact(s)",
        # The grid is the single input that has been wrong three times in two
        # days (FU-205/210/211). Its provenance is printed with every verdict
        # so a stale grid is visible in the same glance as the misses it causes.
        f"grid basis  : {GRID_BASIS}",
        "",
    ]
    if report.get("foreign_evidence"):
        lines.append(
            "foreign evidence (ADVISORY, excluded from the orphan test -- another"
            " lane's dry-run in the shared evidence dir):"
        )
        for item in report["foreign_evidence"]:
            lines.append(
                f"  {item['checked_utc']}  lane={item['lane']}  {item['path']}"
            )
        lines.append("")

    if report["orphan_evidence"]:
        lines.append("ORPHAN EVIDENCE -- verification ran, state never recorded it:")
        for orphan in report["orphan_evidence"]:
            lines.append(f"  {orphan['checked_utc']}  {orphan['path']}")
        lines.append("")
    if report["missed_slots"]:
        lines.append("MISSED SLOTS -- cron came due and left no trace at all:")
        for slot in report["missed_slots"]:
            lines.append(f"  {slot}")
        # A MISSED list has TWO causes that are indistinguishable from here: the
        # task did not run, or THIS FILE'S SLOT GRID IS STALE. The offsets below
        # separate them -- a stale grid shows every receipt PRESENT and all of
        # them sitting at the same wrong offset. Printed beside the misses on
        # purpose, so the distinction reaches the reader instead of waiting for
        # an audit. (This grid has gone stale twice: 2026-07-30 and 07-31.)
        if report.get("grid_phase"):
            lines.append("  -- receipt phase vs nearest declared slot (a UNIFORM"
                         " non-zero offset means the GRID is wrong, not the task):")
            for r, slot, delta in report["grid_phase"]:
                lines.append("     %s  nearest %s  %+.0f min" % (r, slot, delta))
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
