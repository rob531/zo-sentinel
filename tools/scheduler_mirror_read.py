"""Read the live scheduler cron from the tower's mirror. READ-ONLY.

WHY (FU-213). The prod-drift slot grid has been re-broken three times in two
days -- FU-205 (`SLOT_MINUTE` went stale), FU-210 (the cadence was cut and the
grid still described the old one), FU-211 (the replacement read the cron as UTC
when the scheduler evaluates it LOCAL). Every one of those fixes retyped the
literal correctly and left the NEXT drift just as undetectable. FU-211's own
log declined to fix the residue and named it: *"a value re-broken three times
in two days should be fetched, not typed."*

It could not be fetched from a CLI process, and that is still true: the cron
lives behind the `list_scheduled_tasks` MCP tool, and no on-disk JSON under
%APPDATA%/%LOCALAPPDATA%/~/.claude carries `cronExpression`. So the follow-up
triage agent -- which runs daily and DOES hold that tool -- writes the payload
to a mirror file, and this module reads it.

THE CONTRACT IS DELIBERATELY WEAK, because a mirror is allowed to be stale:

  * missing / malformed / stale / unmodellable  -> return None, with a reason
  * present and fresh                           -> return slots, with a basis

A caller MUST keep its own literal as the fallback and MUST print which of the
two it used (R5: publish the basis with the number). A mirror that silently
replaced the literal would move the same defect one level up -- the grid would
still be a value nobody was watching, just in a different file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                                       # pragma: no cover
    ZoneInfo = None

#: Overridable so a test (or a differently-laid-out box) is not another literal.
MIRROR_PATH = os.environ.get(
    "ZO_SCHEDULER_MIRROR",
    r"D:\zo\Zocomputer Agents\_state\scheduler_mirror.json")

DEFAULT_MAX_AGE_HOURS = 36.0


def load_mirror(path: str = None):
    """Return (doc, age_hours, fresh). Never raises."""
    path = path or MIRROR_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None, None, False
    try:
        gen = datetime.fromisoformat(doc["generated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError, TypeError):
        return doc, None, False
    age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600.0
    return doc, age, age <= DEFAULT_MAX_AGE_HOURS


def local_slots_to_utc(slots, on_date, tz_name: str) -> tuple:
    """LOCAL (hour, minute) -> UTC (hour, minute) FOR A GIVEN DATE.

    Date-aware because the offset changes at the DST boundary; a conversion
    that ignores the date is how a correct grid silently becomes an hour wrong
    on 2026-11-01.
    """
    if ZoneInfo is None:                                  # pragma: no cover
        raise RuntimeError("zoneinfo unavailable")
    tz = ZoneInfo(tz_name)
    out = set()
    for hh, mm in slots:
        local = datetime(on_date.year, on_date.month, on_date.day,
                         int(hh), int(mm), tzinfo=tz)
        u = local.astimezone(timezone.utc)
        out.add((u.hour, u.minute))
    return tuple(sorted(out))


def utc_slots_for(task_id: str, on_date=None, path: str = None):
    """(slots, basis) for a task, or (None, reason).

    `basis` / `reason` is never empty: the caller prints it, so the provenance
    of the grid travels with the grid.
    """
    doc, age, fresh = load_mirror(path)
    if doc is None:
        return None, "mirror absent or unreadable (%s)" % (path or MIRROR_PATH)
    task = (doc.get("tasks") or {}).get(task_id)
    if not task:
        return None, "mirror carries no task %r" % task_id
    if task.get("parse_error"):
        return None, "mirror cannot model that cron: %s" % task["parse_error"]
    local = [tuple(s) for s in (task.get("local_slots") or ())]
    if not local:
        return None, "mirror has no local_slots for %r" % task_id
    if not fresh:
        return None, ("mirror is STALE (%.1fh, ceiling %.0fh) -- refusing to "
                      "grade against a schedule nobody has confirmed since"
                      % (age if age is not None else -1, DEFAULT_MAX_AGE_HOURS))
    on_date = on_date or datetime.now(timezone.utc).date()
    slots = local_slots_to_utc(local, on_date, doc.get("tz", "America/New_York"))
    return slots, ("mirror %s (age %.1fh), cron %r evaluated %s"
                   % (doc.get("generated_at"), age,
                      task.get("cronExpression"),
                      doc.get("tz", "America/New_York")))
