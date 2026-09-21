#!/usr/bin/env python3
"""Idempotently grant the scheduled-tasks MCP tools in ~/.claude/settings.json.

Chairman ruling 2026-07-28: a Protean task must be able to change its own SHAPE
and ROLE unattended. The approval prompt on `update_scheduled_task` made every
self-modification a synchronous dependency on the chairman, which is exactly the
allopoietic reflex the charter forbids.

Scope granted (chairman's explicit choice, full autonomy incl. delete):
    mcp__scheduled-tasks__list_scheduled_tasks
    mcp__scheduled-tasks__create_scheduled_task
    mcp__scheduled-tasks__update_scheduled_task
    mcp__scheduled-tasks__delete_scheduled_task

The compensating control is NOT a restriction -- it is RECOVERY. See
_tools/snapshot_scheduled_tasks.ps1: every prompt is snapshotted daily and
before every self-edit, so a bad rewrite or a deletion is text-recoverable.

Re-runnable. Prints what it changed and validates the JSON round-trip.
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

SETTINGS = os.path.expandvars(r"%USERPROFILE%\.claude\settings.json")

GRANT = [
    "mcp__scheduled-tasks__list_scheduled_tasks",
    "mcp__scheduled-tasks__create_scheduled_task",
    "mcp__scheduled-tasks__update_scheduled_task",
    "mcp__scheduled-tasks__delete_scheduled_task",
]


def main() -> int:
    if not os.path.exists(SETTINGS):
        print("FATAL: no settings.json at %s" % SETTINGS)
        return 1

    with open(SETTINGS, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("FATAL: settings.json is not valid JSON, refusing to touch it: %s" % exc)
        return 1

    perms = cfg.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if not isinstance(allow, list):
        print("FATAL: permissions.allow is %s, expected list" % type(allow).__name__)
        return 1

    added = [g for g in GRANT if g not in allow]
    if not added:
        print("no-op: all %d scheduled-tasks grants already present (idempotent)" % len(GRANT))
        return 0

    # Timestamped backup beside the file, in addition to the caller's backup.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(SETTINGS, SETTINGS + ".%s.bak" % stamp)

    allow.extend(added)

    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")

    # Round-trip validate BEFORE swapping in. Never leave a broken settings.json.
    with open(tmp, encoding="utf-8") as fh:
        check = json.load(fh)
    for g in GRANT:
        assert g in check["permissions"]["allow"], g
    assert check["permissions"].get("deny") == cfg["permissions"].get("deny")

    os.replace(tmp, SETTINGS)

    print("GRANTED %d new:" % len(added))
    for a in added:
        print("   +", a)
    print("allow rules: %d  deny rules: %d  defaultMode: %s"
          % (len(check["permissions"]["allow"]),
             len(check["permissions"].get("deny", [])),
             check["permissions"].get("defaultMode")))
    print("backup: %s.%s.bak" % (SETTINGS, stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
