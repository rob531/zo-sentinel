#!/usr/bin/env python3
r"""Per-lane halt sentinel -- the actuator the queue census reports into.

WHY A SENTINEL PER LANE, AND NOT A FIELD IN A SHARED FILE
---------------------------------------------------------
2026-07-30 concurrency eval: 23 of 33 scheduled prompts write shared state and
NONE of them takes the lock that exists for exactly that purpose
(`_tools/fu_lock.py`, built 7/28 after a real observed clobber). Every writer does
read-whole-file -> mutate -> write-whole-file, which loses updates silently.

So a halt must not be a key inside a document. It is ONE FILE PER LANE, created
with O_EXCL -- atomic create IS the compare-and-swap, and a write to lane A cannot
lose-update lane B because they are different files. This is the standing
convention made mechanical: a guardrail halts THAT LANE ONLY, never the surface.

WHY EVERY HALT CARRIES A SHA AND AN EXPIRY
------------------------------------------
* `decided_on_sha` -- on the same day, `prod_deploy_state.json` was written at
  13:49Z certifying a sha that had been HEAD at 10:49Z. A decision that does not
  record what it was decided ON cannot be checked for staleness later, and a halt
  decided on a 3h-old queue read is the same defect wearing a different hat.
* `expires_at` -- a halt that cannot lapse is a permanently-red gate with a
  process attached. The repo has several and they are all ignored. TTL means the
  failure mode is "re-raises next census", not "silently blocks forever".
* `session` -- right now nothing on this tower can answer "which agent did this";
  concurrent agents all appear as the same GitHub identity.

SHADOW MODE IS THE DEFAULT AND IT CANNOT ACT
--------------------------------------------
`mode="shadow"` writes to a SEPARATE directory that `is_halted()` never reads.
Not a flag checked at the point of action -- a different destination, so there is
no code path on which a shadow decision can stop a lane. `test_a_shadow_halt_can_never_block`
is the assertion that proves it, and it is the one test that must never be deleted.

Extends the `tools/shadow_decision.py` convention (record what you WOULD have
decided, act on nothing, reconcile later) rather than inventing a second one.

WHAT ARMING DOES NOT DO (read this before trusting it)
------------------------------------------------------
Arming makes the census WRITE a real halt sentinel. It does not, by itself, stop
anything: a sentinel is only enforcement once a caller consults it. That caller is
one line --

    python tools/lane_halt.py --enforce <lane> || exit 0

-- and until a lane adds it, an armed halt is a loud, dated, queryable record and
nothing more. Saying "the halt is armed" without saying that would be exactly the
"a merge is not an arming" defect this repo keeps paying for.

USAGE
    python tools/lane_halt.py --status                       # all lanes
    python tools/lane_halt.py --raise builder:manifest --reason "..." --sha abc123
    python tools/lane_halt.py --clear builder:manifest --who chairman
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HALT_DIR = os.path.join(ROOT, "artifacts", "lane_halts")
SHADOW_DIR = os.path.join(HALT_DIR, "_shadow")

DEFAULT_TTL_HOURS = 12.0
MODE_SHADOW = "shadow"
MODE_ARMED = "armed"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _slug(lane: str) -> str:
    """`builder:manifest` -> `builder__manifest`. A lane name is not a filename."""
    return re.sub(r"[^A-Za-z0-9_.-]", "__", lane)


def halt_path(lane: str, halt_dir: str | None = None) -> str:
    return os.path.join(halt_dir or HALT_DIR, "%s.json" % _slug(lane))


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _expired(rec: dict, now: dt.datetime) -> bool:
    try:
        return dt.datetime.fromisoformat(rec["expires_at"]) <= now
    except (KeyError, TypeError, ValueError):
        # A record we cannot date is not a record we may trust to keep blocking.
        # Unknown is not zero, but an unreadable expiry must fail OPEN here: the
        # alternative is a corrupt file wedging a lane with no way to reason about it.
        return True


def is_halted(lane: str, now: dt.datetime | None = None,
              halt_dir: str | None = None) -> bool:
    """Live check. Reads ONLY the armed directory -- never the shadow one."""
    rec = _read(halt_path(lane, halt_dir))
    if rec is None:
        return False
    return not _expired(rec, now or _now())


def raise_halt(lane: str, reason: str, sha: str | None = None,
               ttl_hours: float = DEFAULT_TTL_HOURS, session: str = "",
               source: str = "queue_census", mode: str = MODE_SHADOW,
               now: dt.datetime | None = None,
               halt_dir: str | None = None, shadow_dir: str | None = None) -> dict:
    """Raise (or shadow-record) a halt for one lane.

    Idempotent: an existing, unexpired armed halt is returned untouched with
    `raised=False`. Re-raising must not extend a halt's life by accident, or a lane
    that alarms every hour would never lapse and the TTL would be decorative.
    """
    now = now or _now()
    rec = {
        "lane": lane,
        "reason": reason,
        "decided_on_sha": sha,
        "decided_at": now.isoformat(),
        "expires_at": (now + dt.timedelta(hours=ttl_hours)).isoformat(),
        "session": session or os.environ.get("CLAUDE_SESSION_ID", ""),
        "source": source,
        "mode": mode,
    }

    if mode == MODE_SHADOW:
        # A separate destination, not a branch at the point of action. There is no
        # path on which this file can be read by is_halted().
        d = shadow_dir or SHADOW_DIR
        os.makedirs(d, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        p = os.path.join(d, "%s-%s.json" % (_slug(lane), stamp))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rec, fh, indent=1)
        return dict(rec, raised=False, shadowed=True, path=p)

    d = halt_dir or HALT_DIR
    os.makedirs(d, exist_ok=True)
    p = halt_path(lane, d)
    existing = _read(p)
    if existing is not None and not _expired(existing, now):
        return dict(existing, raised=False, shadowed=False, path=p,
                    note="already halted; not extended")

    if existing is not None:
        os.replace(p, p + ".expired")
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Lost the race to a sibling. Its halt is as good as ours; converge.
        return dict(_read(p) or rec, raised=False, shadowed=False, path=p,
                    note="lost create race; sibling halt stands")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1)
    return dict(rec, raised=True, shadowed=False, path=p)


def clear(lane: str, who: str = "", halt_dir: str | None = None) -> bool:
    p = halt_path(lane, halt_dir)
    rec = _read(p)
    if rec is None:
        return False
    rec["cleared_at"] = _now().isoformat()
    rec["cleared_by"] = who
    with open(p + ".cleared", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1)
    os.remove(p)
    return True


def list_halts(halt_dir: str | None = None, now: dt.datetime | None = None) -> list:
    d = halt_dir or HALT_DIR
    now = now or _now()
    out = []
    if not os.path.isdir(d):
        return out
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        rec = _read(os.path.join(d, n))
        if rec:
            out.append(dict(rec, expired=_expired(rec, now)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="per-lane halt sentinel")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--raise", dest="raise_lane")
    ap.add_argument("--reason", default="")
    ap.add_argument("--sha")
    ap.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    ap.add_argument("--armed", action="store_true",
                    help="actually halt (default is shadow, which cannot block)")
    ap.add_argument("--enforce", dest="enforce_lane",
                    help="exit 1 if this lane is halted -- the one-line gate a\nlane calls on itself before doing work")
    ap.add_argument("--clear", dest="clear_lane")
    ap.add_argument("--who", default="")
    a = ap.parse_args(argv)

    if a.enforce_lane:
        if is_halted(a.enforce_lane):
            rec = _read(halt_path(a.enforce_lane)) or {}
            print("HALTED %s: %s (expires %s)"
                  % (a.enforce_lane, rec.get("reason", ""), rec.get("expires_at")))
            return 1
        print("clear: %s" % a.enforce_lane)
        return 0
    if a.clear_lane:
        print("cleared" if clear(a.clear_lane, a.who) else "no active halt")
        return 0
    if a.raise_lane:
        if a.armed and not a.reason.strip():
            print("refusing: an armed halt with no reason is unreviewable")
            return 2
        r = raise_halt(a.raise_lane, a.reason, a.sha, a.ttl_hours,
                       mode=MODE_ARMED if a.armed else MODE_SHADOW)
        print(json.dumps(r, indent=1))
        return 0

    halts = list_halts()
    if not halts:
        print("no lane halts recorded")
    for h in halts:
        print("%-22s %-8s %s expires=%s\n    %s"
              % (h["lane"], "EXPIRED" if h["expired"] else "ACTIVE",
                 h.get("decided_on_sha") or "-", h.get("expires_at"), h.get("reason")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
