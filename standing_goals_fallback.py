#!/usr/bin/env python3
"""
standing_goals_fallback.py

Fallback directive emitter. When sentinel_directive_generator's LLM-suggested
set fully dedupes (every suggestion already queued/built), this module supplies
the next un-built goal from standing_goals.json so the builder is NEVER idle.

Contract
--------
emit_standing_goals(directive_dir: Path, max_n: int = 3) -> list[dict]
  Returns up to `max_n` directive dicts (validated against the same schema the
  generator uses) for goals that:
    1. Have no built file at /home/workspace/zo_sentinel/<output_file>
    2. Have no active or .done. directive file matching the task name
  Goals are returned in descending priority order.

The caller (run_cycle in sentinel_directive_generator) is responsible for
actually writing the directive files via its existing write_directive() helper
so normal validation and queueing flow applies.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("directive_gen.standing_goals")

STANDING_GOALS_PATH = Path("/home/workspace/zo_sentinel/standing_goals.json")
BUILD_DIR = Path("/home/workspace/zo_sentinel")

REQUIRED_FIELDS = ("task", "output_file", "handler", "description")


def _load_goals() -> list[dict]:
    if not STANDING_GOALS_PATH.exists():
        log.warning("standing_goals.json not found at %s", STANDING_GOALS_PATH)
        return []
    try:
        data = json.loads(STANDING_GOALS_PATH.read_text())
    except Exception as e:
        log.error("standing_goals.json parse error: %s", e)
        return []
    goals = data.get("goals", [])
    if not isinstance(goals, list):
        log.error("standing_goals.json 'goals' is not a list")
        return []
    # Sanity-check each entry
    valid = []
    for g in goals:
        if not isinstance(g, dict):
            continue
        if all(k in g for k in REQUIRED_FIELDS):
            valid.append(g)
        else:
            log.warning("standing_goals: dropping incomplete entry %r",
                        g.get("task", "<no-task>"))
    valid.sort(key=lambda g: g.get("priority", 0.5), reverse=True)
    return valid


def _is_already_built(output_file: str) -> bool:
    """Goal is satisfied if the target file already exists in BUILD_DIR."""
    return (BUILD_DIR / output_file).exists()


def _is_already_queued(directive_dir: Path, task: str) -> bool:
    """Goal is in flight if any directive file (active OR .done.) matches the task.

    We deliberately treat .done. files as 'already queued' here so we don't
    re-emit a goal whose previous build attempt was completed (the file existence
    check above also catches successes; this catches builds in flight or in the
    .done. archive whose code may not have reached BUILD_DIR yet).
    """
    matches = list(directive_dir.glob(f"*{task[:30]}*.json"))
    return bool(matches)


def emit_standing_goals(directive_dir: Path, max_n: int = 3) -> list[dict]:
    """Return up to max_n directive dicts for un-built standing goals.

    Pure read; never writes anything itself. The caller's write_directive()
    handles persistence so all the existing logging and validation paths apply.
    """
    if max_n <= 0:
        return []
    goals = _load_goals()
    if not goals:
        return []

    emitted: list[dict] = []
    skipped_built = 0
    skipped_queued = 0
    for g in goals:
        if len(emitted) >= max_n:
            break
        output = g.get("output_file", "")
        task = g.get("task", "")
        if _is_already_built(output):
            skipped_built += 1
            continue
        if _is_already_queued(directive_dir, task):
            skipped_queued += 1
            continue
        # Strip helper-only metadata before handing to write_directive
        clean = {k: v for k, v in g.items() if not k.startswith("_") and k != "closes_gap"}
        clean.setdefault("complexity", "medium")
        clean.setdefault("priority", 0.5)
        emitted.append(clean)

    log.info(
        "standing_goals: emitted=%d skipped_built=%d skipped_queued=%d total_goals=%d",
        len(emitted), skipped_built, skipped_queued, len(goals),
    )
    return emitted


if __name__ == "__main__":
    # Self-test: print what would be emitted right now
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = emit_standing_goals(Path("/home/workspace/zo_sentinel/directives"), max_n=10)
    print(json.dumps(out, indent=2))