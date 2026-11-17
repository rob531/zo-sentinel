#!/usr/bin/env python3
"""builder_stall_alarm.py -- LOUD, falsifiable alarm for the recurring builder dark period.

The zo-sentinel builder goes dark every ~2-3 days from an ever-changing root cause
(ghost .done, .failed graveyard, keyless shim, pending-cap deadlock, orphaned chains, ...).
Each has had a "self-healing" janitor; each recurred from a NEW cause. The ONE thing that
has NOT existed is a loud alarm that fires on the SYMPTOM regardless of cause -- so the
chairman keeps being the detector-of-last-resort (noticing "builder's been quiet" by hand).

This is that alarm. Symptom-level + cause-agnostic:
  the builder makes a model call for EVERY build, captured at the shim choke-point in
  goose_prompt_response.jsonl. If that file has not been written for > STALE_HOURS while
  buildable work exists (pending/ or the quarantine backlog -- essentially always), the
  builder is DARK. That is true no matter WHICH new cause jammed it.

LOUD on fire (not a silent self-heal): a flag file, a RED log line, a fleet-readable
mesh event (best-effort via the write_service), and a NONZERO exit so any cron/monitor
surfaces it. Clears the flag + logs green when healthy. Deterministic; no model calls.

    python tools/builder_stall_alarm.py            # check now (exit 1 = ALARM)
    BUILDER_STALL_HOURS=3 python tools/builder_stall_alarm.py
Schedule it (independent of goose, every ~10 min):
    */10 * * * * python3 /home/workspace/zo_sentinel/tools/builder_stall_alarm.py \
                 >> /home/workspace/logs/builder_stall_alarm.log 2>&1
"""
from __future__ import annotations

import glob
import json
import os
import time

SHADOW_LOG = os.environ.get("SHADOW_LOG", "/home/workspace/logs/goose_prompt_response.jsonl")
PENDING_DIR = os.environ.get("PENDING_DIR", "/home/workspace/zo_sentinel/directives/pending")
QUARANTINE_GLOB = os.environ.get(
    "QUARANTINE_GLOB", "/home/workspace/zo_sentinel/directives/*.ghost.json")
FLAG = os.environ.get("STALL_FLAG", "/home/workspace/logs/BUILDER_STALL.alarm")
STALE_HOURS = float(os.environ.get("BUILDER_STALL_HOURS", "3"))
WRITE_SERVICE = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772")


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def assess(now: float | None = None) -> dict:
    """Pure: compute the alarm verdict from the filesystem. now injectable for tests."""
    now = time.time() if now is None else now
    try:
        shadow_age_h = (now - os.path.getmtime(SHADOW_LOG)) / 3600.0
        shadow_seen = True
    except OSError:
        shadow_age_h, shadow_seen = float("inf"), False
    pending = len(glob.glob(os.path.join(PENDING_DIR, "*.json")))
    quarantined = len(glob.glob(QUARANTINE_GLOB))
    work_exists = (pending + quarantined) > 0
    dark = shadow_age_h > STALE_HOURS
    alarm = bool(dark and work_exists)
    if alarm:
        reason = ("builder DARK: no shim model-call for %.1fh (> %.1fh) while %d pending + "
                  "%d quarantined directives await -- builds are stalled, cause-agnostic"
                  % (shadow_age_h, STALE_HOURS, pending, quarantined))
    elif dark and not work_exists:
        reason = "builds idle %.1fh but NO work queued -- legitimately idle, not stalled" % shadow_age_h
    else:
        reason = "healthy: last build %.2fh ago, %d pending, %d quarantined" % (
            shadow_age_h if shadow_seen else -1, pending, quarantined)
    return {"alarm": alarm, "reason": reason, "shadow_age_hours": round(shadow_age_h, 2),
            "pending": pending, "quarantined": quarantined, "stale_hours": STALE_HOURS,
            "shadow_seen": shadow_seen, "checked_at": _utc()}


def _emit_event(v: dict) -> None:
    """Best-effort fleet-readable event via the write_service; never raises."""
    try:
        import requests
        requests.post("%s/write" % WRITE_SERVICE.rstrip("/"), timeout=5, json={
            "table": "mesh_memory",
            "id": "builder_stall_alarm_%s" % v["checked_at"],
            "agent_id": "zo_sentinel.alarm",
            "memory_type": "builder_stall_alarm",
            "importance": 0.95,
            "content": json.dumps(v),
        })
    except Exception:
        pass


def run(apply: bool = True, log=print) -> dict:
    v = assess()
    line = "[builder-stall-alarm] %s %s" % ("ALARM" if v["alarm"] else "ok", v["reason"])
    log(line)
    if not apply:
        return v
    if v["alarm"]:
        try:
            with open(FLAG, "w", encoding="utf-8") as fh:
                json.dump(v, fh, indent=1)
        except OSError:
            pass
        _emit_event(v)
    else:
        # clear a stale flag on recovery so the alarm is edge-accurate
        try:
            os.remove(FLAG)
        except OSError:
            pass
    return v


def main(argv=None) -> int:
    v = run(apply=True)
    return 1 if v["alarm"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
