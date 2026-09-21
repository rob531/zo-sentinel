#!/usr/bin/env python3
"""ops_audit_state.py -- the daily ops audit's spend memory.

WHY THIS EXISTS (two bugs it fixes, both silent):

1. HISTORY WAS NEVER KEPT. The state file was written as a single
   ``{"date": ..., "balance": ...}`` object and OVERWRITTEN every run, so
   "month-to-date spend = balance delta since the first entry of this month"
   could only ever see yesterday. The audit reported an MTD number that was
   really a 24-hour delta, and no one could tell from the output.

2. THE DELTA IS WRONG ACROSS A TOP-UP. Balance goes UP when credit is added,
   so a mid-month top-up makes naive ``first - current`` go NEGATIVE and the
   month reads as though money was earned. Spend is::

       spend = (first_balance + credits_added_in_window) - current_balance

   Credit events must therefore be recorded too, not just balances.

Schema v2::

    {"schema": 2,
     "entries": [{"date": "2026-07-27", "at": "<iso>", "balance": 17.135}],
     "credits": [{"date": "2026-07-17", "at": "<iso>", "amount": 25.0,
                  "id": 3148330, "source": "vast_invoice"}]}

Schema v1 (the bare single object) is migrated on load, so the one data
point that survived is not thrown away.

IDEMPOTENT BY CHARACTER: recording twice on the same date UPDATES that day's
entry instead of appending a duplicate, and a corrupt/absent file is rebuilt
rather than raised on -- a run that hits the break repairs it.

CLI::

    python tools/ops_audit_state.py record --balance 17.135 [--date YYYY-MM-DD]
    python tools/ops_audit_state.py credit --amount 25 --date 2026-07-17 --id 3148330
    python tools/ops_audit_state.py show [--month YYYY-MM]

Path resolution: ``--path`` > ``$ZO_OPS_AUDIT_STATE`` > ``D:/zo/runs/ops_audit_state.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(os.environ.get("ZO_OPS_AUDIT_STATE",
                                   r"D:/zo/runs/ops_audit_state.json"))
SCHEMA = 2

# THE BUDGET LINE, IN CODE (FU-035).
# Chairman ruling 2026-07-17: $25 must last the month; alert at >=$20 spent.
# Judged against since_funding (exact for a prepaid account), NOT the MTD
# delta, whose basis can be a few days old and structurally understate burn.
BUDGET_USD = 25.0
RED_AT_USD = 20.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def empty_state() -> dict:
    return {"schema": SCHEMA, "entries": [], "credits": []}


def load(path: Optional[Path] = None) -> dict:
    """Read state, migrating v1 and healing corruption. NEVER raises for a
    bad file: the audit's job is to keep auditing, and a state file that can
    kill the run is worse than one that lost a day."""
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(empty_state(), migrated_from="unreadable")
    if isinstance(raw, dict) and "balance" in raw and "entries" not in raw:
        # v1: a single overwritten sample. Keep it -- it is a real datapoint.
        return {"schema": SCHEMA, "credits": [],
                "entries": [{"date": raw.get("date") or _today()[:10],
                             "at": raw.get("at") or "",
                             "balance": float(raw["balance"])}],
                "migrated_from": 1}
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return dict(empty_state(), migrated_from="malformed")
    raw.setdefault("schema", SCHEMA)
    raw.setdefault("credits", [])
    return raw


def save(state: dict, path: Optional[Path] = None) -> Path:
    path = Path(path or DEFAULT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["entries"] = sorted(state.get("entries", []), key=lambda e: e["date"])
    state["credits"] = sorted(state.get("credits", []), key=lambda c: c["date"])
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def _in_memory(path, state) -> bool:
    """True when the caller supplied the state and named no file to write.

    WHY THIS EXISTS (2026-08-06, FU-268, live incident): `record()` and
    `record_credit()` ended `if path is not False: save(state, path)`, and
    `save()` resolves `path or DEFAULT_PATH`. So a caller who passed an explicit
    in-memory `state=` and no `path=` -- the natural way to write a read-only
    probe -- silently PERSISTED that partial state over the canonical file. It
    destroyed D:\\zo\\runs\\ops_audit_state.json: 11 balance samples and
    `schema: 2` deleted, a real top-up re-dated, a fabricated invoice appended,
    and `budget.level` blinded to UNKNOWN four hours after FU-267 had repaired
    that very number. The same file had already been clobbered once, on
    2026-07-28, by a different lane.

    A `path=False` no-write mode already existed, but it is opt-in and invisible
    in a signature typed `Optional[Path]`. The DESTRUCTIVE behaviour was the
    DEFAULT and the safe one had to be known in advance. This inverts that:
    supplying a state means "operate on THIS", and persisting is the thing you
    ask for, by naming a path.

    Zero existing callers change. At 9e12c062 `git grep record_credit` is 16
    test calls (all `path=statefile`) plus one CLI call (`path=path`), and every
    `record()` call site likewise passes `path=`. Nothing in the repo relied on
    the implicit DEFAULT_PATH write.
    """
    return state is not None and path is None


def record(balance: float, date: Optional[str] = None,
           path: Optional[Path] = None, state: Optional[dict] = None) -> dict:
    """Upsert today's balance sample. Same-date re-run replaces, never appends.

    Persists to `path`, or to DEFAULT_PATH when no `state=` was supplied.
    Passing `state=` without `path=` is IN-MEMORY and writes nothing (FU-268);
    `path=False` remains an explicit no-write for callers that pass both.
    """
    in_memory = _in_memory(path, state)
    state = state if state is not None else load(path)
    date = date or _today()
    entry = {"date": date, "at": _utcnow(), "balance": float(balance)}
    state["entries"] = [e for e in state.get("entries", []) if e["date"] != date]
    state["entries"].append(entry)
    if path is not False and not in_memory:
        save(state, path)
    return state


def _cid(v):
    """Normalise a credit id to a comparable form.

    WHY THIS EXISTS (2026-08-06, this lane, live incident): the dedup key was
    the RAW value, so an id written as int 3148330 by a Python caller did not
    equal the SAME invoice arriving as str "3148330" from argparse (which has
    no type=). Vast invoice 3148330 was therefore recorded twice, credits_ever
    went $25 -> $50, since_funding.spend_usd went $10.23 -> $35.23, and
    budget.level flipped to a FALSE RED against a $25 budget on an account
    that has only ever been funded once. Same class as the permission value
    graded against one literal: a key that is compared must be NORMALISED at
    both ends, never trusted to arrive in one type.
    """
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def record_credit(amount: float, date: str, credit_id=None,
                  source: str = "vast_invoice", path: Optional[Path] = None,
                  state: Optional[dict] = None) -> dict:
    """Record a top-up. Deduped on (id) when known, else (date, amount).

    The id is compared via _cid() so int and str spellings of the same
    invoice collapse to one entry. Also self-heals: pre-existing duplicates
    of the id being recorded are dropped on write.

    Persists to `path`, or to DEFAULT_PATH when no `state=` was supplied.
    Passing `state=` without `path=` is IN-MEMORY and writes nothing (FU-268).
    """
    in_memory = _in_memory(path, state)
    state = state if state is not None else load(path)
    credit_id = _cid(credit_id)
    key = ("id", credit_id) if credit_id is not None else ("da", date, float(amount))
    def _key(c):
        return (("id", _cid(c.get("id"))) if _cid(c.get("id")) is not None
                else ("da", c["date"], float(c["amount"])))
    state["credits"] = [c for c in state.get("credits", []) if _key(c) != key]
    state["credits"].append({"date": date, "at": _utcnow(),
                             "amount": float(amount), "id": credit_id,
                             "source": source})
    if path is not False and not in_memory:
        save(state, path)
    return state


def month_to_date(state: dict, month: Optional[str] = None) -> dict:
    """Spend since the first balance sample of `month` (default: this month).

    Returns a dict that is HONEST about its own basis: `basis_days` and
    `first_entry_date` let the reader see whether "MTD" really covers the
    month or only the days since the history began. A number whose provenance
    you cannot see is the thing this module exists to stop shipping.
    """
    month = month or _today()[:7]
    entries = sorted((e for e in state.get("entries", [])
                      if e["date"][:7] == month), key=lambda e: e["date"])
    if not entries:
        return {"month": month, "spend_usd": None, "basis": "no entries",
                "first_entry_date": None, "current_balance": None,
                "credits_added": 0.0, "basis_days": 0, "complete_month": False}
    first, last = entries[0], entries[-1]
    credits = round(sum(float(c["amount"]) for c in state.get("credits", [])
                        if c["date"][:7] == month
                        and c["date"] >= first["date"]), 4)
    spend = round(first["balance"] + credits - last["balance"], 4)
    basis_days = (datetime.strptime(last["date"], "%Y-%m-%d")
                  - datetime.strptime(first["date"], "%Y-%m-%d")).days
    return {"month": month,
            "spend_usd": spend,
            "current_balance": last["balance"],
            "first_entry_date": first["date"],
            "first_entry_balance": first["balance"],
            "credits_added": credits,
            "basis_days": basis_days,
            "observed_days": len({e["date"] for e in entries}),
            "missing_days": (basis_days + 1
                             - len({e["date"] for e in entries})),
            "complete_month": first["date"].endswith("-01"),
            "basis": ("first_balance + credits - current_balance over "
                      f"{basis_days}d from {first['date']}")}


def coverage(state: dict, month: Optional[str] = None) -> dict:
    """Which days this lane actually OBSERVED -- and which it did not.

    The daily ops audit is the only writer of ``entries[]``, so the dates
    present in it are a record of this lane's own runs. A date between the
    first and last entry that is ABSENT means the audit did not run that day.

    SPANS THE WHOLE HISTORY BY DEFAULT, AND THAT IS THE POINT. The first
    draft of this function was month-scoped, and it could not see the gap
    that motivated it: the state file held 2026-07-26..2026-07-30 and then
    2026-08-01, so July read COMPLETE, August read COMPLETE, and the missed
    day fell in the seam between them. A guard that cannot catch what it
    guards is worse than no guard, because it reports clean. ``month=`` is
    available for a scoped read, but ``show`` must never pass one.

    Why this is not cosmetic: ``month_to_date`` reports ``basis_days`` as the
    CALENDAR span from first entry to last, so a window with holes publishes
    the same basis as a fully observed one. A reader cannot tell a 30-day
    window sampled 30 times from a 30-day window sampled twice. Publishing
    the basis (R5) means publishing how much of it was actually looked at,
    and an unobserved day is UNKNOWN, not zero (R6).

    REPORT, NOT A GATE: nothing here changes an exit code or blocks a run.
    A missed day is often legitimate (a paused fleet, a month boundary); the
    value is that it becomes VISIBLE instead of being absorbed into a span.
    """
    dates = sorted({e["date"] for e in state.get("entries", [])
                    if month is None or e["date"][:7] == month})
    scope = month or "all"
    if not dates:
        return {"scope": scope, "observed_days": 0, "span_days": 0,
                "missing_dates": [], "complete": None,
                "basis": "no entries in scope -- coverage is UNKNOWN, "
                         "not complete"}
    first = datetime.strptime(dates[0], "%Y-%m-%d")
    last = datetime.strptime(dates[-1], "%Y-%m-%d")
    span = (last - first).days + 1
    have = set(dates)
    missing = [(first + timedelta(days=i)).strftime("%Y-%m-%d")
               for i in range(span)
               if (first + timedelta(days=i)).strftime("%Y-%m-%d") not in have]
    return {"scope": scope,
            "observed_days": len(dates),
            "span_days": span,
            "first_entry_date": dates[0],
            "last_entry_date": dates[-1],
            "missing_dates": missing,
            "complete": not missing,
            "basis": ("dates present in entries[] vs every date in "
                      f"{dates[0]}..{dates[-1]}; a missing date means this "
                      "lane did not run that day (FU-207 class)")}


def since_funding(state: dict) -> dict:
    """EXACT burn for a prepaid account, independent of sample history.

    ``credits_ever - current_balance``. Needs no month boundary and no dense
    history: it cannot be understated by a state file that only started
    yesterday, which is exactly how the monthly delta can mislead. Returns
    ``spend_usd: None`` when no credit event has been recorded, rather than
    a confident zero.
    """
    entries = sorted(state.get("entries", []), key=lambda e: e["date"])
    credits = state.get("credits", [])
    if not entries or not credits:
        return {"spend_usd": None, "credits_ever": 0.0, "balance": None,
                "basis": "no credit events recorded"}
    credits_ever = round(sum(float(c["amount"]) for c in credits), 4)
    balance = entries[-1]["balance"]
    last_credit = max(credits, key=lambda c: c["date"])
    return {"spend_usd": round(credits_ever - balance, 4),
            "credits_ever": credits_ever,
            "balance": balance,
            "last_funded": last_credit["date"],
            "basis": "credits_ever - current_balance (exact for a prepaid "
                     "account funded from zero)"}


def budget_status(state: dict, budget: float = BUDGET_USD,
                  red_at: float = RED_AT_USD) -> dict:
    """Evaluate burn against the budget line and return a machine-readable verdict.

    Encodes the chairman's $25/month rule so the audit does not depend on a
    reading agent applying a threshold written in prose (FU-035: a guard whose
    only failure mode is a silent all-clear is not a guard).

    FAILS LOUD, NOT OPEN: when no credit event has been recorded the burn is
    unknowable from local state, so ``level`` is ``UNKNOWN`` -- never ``GREEN``.
    An UNKNOWN is a finding the audit must surface, not a pass.
    """
    sf = since_funding(state)
    spend = sf.get("spend_usd")
    if spend is None:
        return {"level": "UNKNOWN", "spend_usd": None, "budget_usd": budget,
                "red_at_usd": red_at, "remaining_usd": None,
                "metric": "since_funding",
                "basis": "no credit events recorded -- burn is not computable "
                         "from local state; treat as a finding, not a pass"}
    return {"level": "RED" if spend >= red_at else "GREEN",
            "spend_usd": spend,
            "budget_usd": budget,
            "red_at_usd": red_at,
            "remaining_usd": round(budget - spend, 4),
            "metric": "since_funding",
            "basis": "since_funding.spend_usd vs red_at (%s)" % sf.get("basis")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["record", "credit", "show"])
    ap.add_argument("--balance", type=float)
    ap.add_argument("--amount", type=float)
    ap.add_argument("--id", dest="credit_id")
    ap.add_argument("--date")
    ap.add_argument("--month")
    ap.add_argument("--path")
    a = ap.parse_args(argv)
    path = Path(a.path) if a.path else None
    if a.cmd == "record":
        if a.balance is None:
            ap.error("record needs --balance")
        state = record(a.balance, a.date, path)
    elif a.cmd == "credit":
        if a.amount is None or not a.date:
            ap.error("credit needs --amount and --date")
        state = record_credit(a.amount, a.date, a.credit_id, path=path)
    else:
        state = load(path)
    print(json.dumps({"path": str(path or DEFAULT_PATH),
                      "entries": len(state.get("entries", [])),
                      "credits": len(state.get("credits", [])),
                      "mtd": month_to_date(state, a.month),
                      "coverage": coverage(state),
                      "since_funding": since_funding(state),
                      "budget": budget_status(state)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
