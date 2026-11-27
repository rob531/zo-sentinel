#!/usr/bin/env python3
"""deferred_aging_report.py -- GR-5: age every deferral and say when the hatch is a graveyard.

THE GAP (LOCO_CHAIRMAN.md 6.1 GR-5, class GC-7 escalation owed, not automatic)
  tools/reachability_deferred.json lets a router stay unmounted "pending the mount-lane
  review". reachability_ratchet.py blocks the list from GROWING (the derivative, armed in
  pr-gates since 2026-09-14), but nothing ages it: on 2026-11-27 26 of 27 entries were
  declared in July/August and the review they wait on never ran. PRODUCT_SPEC's
  deferred_router_ledger_report was meant to do this; what landed under that name is a
  FastAPI route over a model that does not exist, so the aging report had no producer.

WHAT IT REPORTS (stdlib + reachability_ratchet; imports no scanned module, no network)
  per entry: module, declared (YYYY-MM-DD found in the reason, else None), age_days,
             status ACTIVE (still an orphan) | STALE (no longer an orphan -- the entry
             is dead headroom), reasonless, aged (age > ZO_DEFERRAL_MAX_AGE_DAYS,
             default 14; an UNDATED entry counts as aged -- absence is not freshness)
  totals:    declared, active, stale, reasonless, undated, aged, oldest_days,
             cap (ZO_DEFERRED_REVIEW_CAP, default = the ratchet's 40), over_cap
  escalate:  true when over_cap, or any entry is aged, stale or reasonless.

  rc 0 report written (escalate or not -- the nightly acts on `escalate`), 2 UNKNOWN
  (deferred file unreadable). Never 0 on an input it could not read.

  python tools/deferred_aging_report.py [--json] [--out artifacts/deferred_aging.json]
  python tools/deferred_aging_report.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import reachability_ratchet as rr  # noqa: E402

DATE_RE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)")
OK, UNKNOWN = 0, 2


def max_age_days() -> int:
    return int(os.environ.get("ZO_DEFERRAL_MAX_AGE_DAYS", "14"))


def review_cap() -> int:
    return int(os.environ.get("ZO_DEFERRED_REVIEW_CAP", str(rr.DEFERRED_REVIEW_CAP)))


def declared_on(reason: str):
    m = DATE_RE.search(reason or "")
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def build(deferred: dict, orphan_stems: set, today: dt.date,
          max_age: int | None = None, cap: int | None = None) -> dict:
    max_age = max_age_days() if max_age is None else max_age
    cap = review_cap() if cap is None else cap
    rows = []
    for mod in sorted(deferred):
        reason = deferred[mod] or ""
        d = declared_on(reason)
        age = (today - d).days if d else None
        rows.append({
            "module": mod,
            "declared": d.isoformat() if d else None,
            "age_days": age,
            "status": "ACTIVE" if mod in orphan_stems else "STALE",
            "reasonless": not reason.strip(),
            "aged": age is None or age > max_age,
        })
    active = [r for r in rows if r["status"] == "ACTIVE"]
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]
    totals = {
        "declared": len(rows),
        "active": len(active),
        "stale": sum(r["status"] == "STALE" for r in rows),
        "reasonless": sum(r["reasonless"] for r in rows),
        "undated": sum(r["declared"] is None for r in rows),
        "aged": sum(r["aged"] for r in rows),
        "oldest_days": max(ages) if ages else None,
        "max_age_days": max_age,
        "cap": cap,
        "over_cap": len(active) > cap,
    }
    escalate = bool(totals["over_cap"] or totals["aged"] or totals["stale"] or totals["reasonless"])
    return {"generated_on": today.isoformat(), "totals": totals,
            "escalate": escalate, "entries": rows}


def run(today: dt.date | None = None) -> tuple[int, dict]:
    try:
        with open(rr.DEFERRED_PATH, encoding="utf-8") as fh:
            json.load(fh)
    except (OSError, ValueError) as e:
        return UNKNOWN, {"verdict": "UNKNOWN", "reason": f"deferred file unreadable: {e}"}
    deferred = rr.load_deferred()
    orphan_stems = {o["module"] for o in rr.census()["orphans"]}
    return OK, build(deferred, orphan_stems, today or dt.datetime.now(dt.timezone.utc).date())


def markdown(rep: dict) -> str:
    t = rep["totals"]
    lines = [
        f"### Deferral aging ({rep['generated_on']})",
        "",
        f"{t['active']} active deferrals against a review cap of {t['cap']}"
        f" ({'OVER CAP' if t['over_cap'] else 'under cap'}); {t['aged']} older than"
        f" {t['max_age_days']} days, oldest {t['oldest_days']} days;"
        f" {t['stale']} stale, {t['reasonless']} reasonless, {t['undated']} undated.",
        "",
        "| module | declared | age (days) | status | aged |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rep["entries"], key=lambda r: -(r["age_days"] if r["age_days"] is not None else 10**6)):
        lines.append(f"| `{r['module']}` | {r['declared'] or 'undated'} | "
                     f"{r['age_days'] if r['age_days'] is not None else '?'} | {r['status']} | "
                     f"{'yes' if r['aged'] else 'no'} |")
    lines += ["", "Mount each router, retire it, or take it off the list. The hatch is for a",
              "build that structurally cannot mount, not storage."]
    return "\n".join(lines)


def self_test() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(("pass " if cond else "FAIL ") + name)
        ok = ok and cond

    today = dt.date(2026, 11, 27)
    deferred = {
        "fresh": "auto-declared for build 'fresh.py|2026-11-20T00:00:00': cannot mount",
        "old": "auto-declared for build 'old.py|2026-10-28T00:00:00': cannot mount",
        "gone": "auto-declared 2026-11-25: cannot mount",
        "nodate": "declared by hand, no date",
        "blank": "",
    }
    rep = build(deferred, {"fresh", "old", "nodate", "blank"}, today, max_age=14, cap=40)
    by = {r["module"]: r for r in rep["entries"]}
    expect("a 7-day entry is not aged", by["fresh"]["age_days"] == 7 and not by["fresh"]["aged"])
    expect("a 30-day entry is aged", by["old"]["age_days"] == 30 and by["old"]["aged"])
    expect("an undated entry counts as aged, never fresh", by["nodate"]["aged"] and by["nodate"]["declared"] is None)
    expect("an entry that is no longer an orphan is STALE", by["gone"]["status"] == "STALE")
    expect("a reasonless entry is reported, not dropped", by["blank"]["reasonless"] and rep["totals"]["reasonless"] == 1)
    expect("totals", rep["totals"]["stale"] == 1 and rep["totals"]["active"] == 4
           and rep["totals"]["oldest_days"] == 30)
    expect("aged entries escalate", rep["escalate"])
    clean = build({"fresh": deferred["fresh"]}, {"fresh"}, today, max_age=14, cap=40)
    expect("one fresh active entry does not escalate", not clean["escalate"])
    big = build({f"m{i}": "x 2026-11-26" for i in range(41)}, {f"m{i}" for i in range(41)},
                today, max_age=14, cap=40)
    expect("41 fresh active entries are over a cap of 40 and escalate",
           big["totals"]["over_cap"] and big["escalate"])
    at = build({f"m{i}": "x 2026-11-26" for i in range(40)}, {f"m{i}" for i in range(40)},
               today, max_age=14, cap=40)
    expect("exactly 40 is not over cap", not at["totals"]["over_cap"] and not at["escalate"])
    print("SELF-TEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--out", default=None, help="also write the JSON report here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    rc, rep = run()
    if a.out and rc == OK:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    if a.json or rc != OK:
        print(json.dumps(rep, indent=2))
    elif a.markdown:
        print(markdown(rep))
    else:
        t = rep["totals"]
        print(f"deferred_aging_report: escalate={'yes' if rep['escalate'] else 'no'} "
              f"active={t['active']}/{t['cap']} aged={t['aged']} stale={t['stale']} "
              f"reasonless={t['reasonless']} oldest_days={t['oldest_days']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
