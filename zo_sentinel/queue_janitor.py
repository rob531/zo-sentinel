"""
queue_janitor.py -- skip => retire: the durable fix for the queue-saturation
deadlock that starves the architect (2026-07-02 stall, and every recurrence of
"no novel directives" before it).

THE LEAK THIS CLOSES:
  goose_runner.is_goose_eligible SKIPS two directive classes every cycle but
  nothing ever RETIRES them:
    1. dedup-redundant  -- declared output already on disk, no open lesson
       (the flag-gated dedup, PR #1060), and
    2. durably-quarantined -- <id>.failed.json in zo_sentinel_state/quarantine/
       (ghost give-ups).
  Skipped files sit in directives/pending/ forever. The promoter refuses to
  clobber a "non-terminal" pending occupant (correct: it might be in-flight),
  so same-name proposals collide and stay in proposed/. proposed/ pins at
  DGG_MAX_PROPOSED_DEPTH (40) and sentinel_directive_generator_goose logs
  "proposed/ depth N >= cap 40; skipping cycle" indefinitely -> ZERO novel
  directives, however healthy generation itself is. Verified live 2026-07-02:
  clearing 10 quarantine sentinels resumed builds within 60s.

WHAT IT DOES (subtractive only; never touches generation or builds):
  One pass over directives/pending/ and directives/proposed/ retiring, per
  queue:
    - quarantined: the directive's resolved id has a .failed.json quarantine
      sentinel (in-repo directives/ OR the durable store). goose will never
      build it; the promoter (now durable-aware) will never accept a
      re-proposal of it. Pure squatter.
    - redundant: a CREATE directive whose declared output already exists on
      disk with NO open lesson -- the EXACT test goose_runner's dedup skip and
      tools/reap_redundant_pending.py use (build_completion.declared_output +
      build_lessons.open_lessons_for), so what it retires is precisely what
      goose would skip. Edit-class directives (wire_*/integrate_*/... ->
      declared_output None) are never touched.
  Retired files are MOVED (never deleted) to
  directives/retired/<utcstamp>/<class>/ -- reversible by moving them back.
  directive_simplifier._load_original_directive also searches retired/, so
  decomposition of quarantined parents keeps working after retirement.

GATING (default OFF -- merging this changes no runtime behavior):
  env ZO_QUEUE_JANITOR=1, OR sentinel file directives/.queue_janitor_on
  containing "1" (the proven, restart-free #1060 dedup pattern; write "0" to
  disable live). The promoter calls run_pass() at the top of each cycle when
  enabled, so pending/ drains -> collisions clear -> proposed/ drains below
  the cap -> the architect resumes proposing novel work.

Pure stdlib + the pure zo_sentinel helpers; no import-time side effects; no
network; never raises from run_pass. Unit-testable with tmp_path.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from zo_sentinel.build_completion import declared_output, failed_quarantined
from zo_sentinel.build_lessons import open_lessons_for

log = logging.getLogger("queue_janitor")

DEFAULT_DURABLE_QUARANTINE_DIR = "/home/workspace/zo_sentinel_state/quarantine"
SENTINEL_NAME = ".queue_janitor_on"
ENV_FLAG = "ZO_QUEUE_JANITOR"
# Queues swept. done/ is terminal by definition; retired/ is our own output.
QUEUES = ("pending", "proposed")
# Suffixes that mark a file as not-a-live-directive.
_EXCLUDED_SUFFIXES = (".done.json", ".failed.json")


def _durable_quarantine_dir() -> Path:
    """Read env each call so tests (and live ops) can repoint it without a
    daemon restart -- same discipline as the dedup flag."""
    return Path(os.environ.get("ZO_DURABLE_QUARANTINE_DIR",
                               DEFAULT_DURABLE_QUARANTINE_DIR))


def enabled(directives_root) -> bool:
    """Flag-gated, read fresh each call (flip live, no restart): env
    ZO_QUEUE_JANITOR, or sentinel file <directives_root>/.queue_janitor_on."""
    val = os.environ.get(ENV_FLAG, "")
    if val.strip().lower() not in ("", "0", "off", "false"):
        return True
    try:
        sf = Path(directives_root) / SENTINEL_NAME
        return (sf.is_file()
                and sf.read_text(encoding="utf-8").strip().lower()
                not in ("", "0", "off", "false"))
    except Exception:
        return False


def _resolve_directive_id(d: dict) -> str:
    """Mirror goose_runner.resolve_directive_id / the promoter's resolver
    EXACTLY (directive_id | id | key | task) -- identity drift between the
    halves is a documented past deadlock cause."""
    return str(
        d.get("directive_id") or d.get("id") or d.get("key") or d.get("task") or ""
    ).strip()


def _iter_directive_files(queue_dir: Path) -> List[Path]:
    """Live directive JSONs in a queue dir: *.json minus terminal sentinels.
    (.rejected/.duplicate/.skip don't end in .json, so they're excluded by
    construction -- same rule as the promoter's _iter_proposals.)"""
    if not queue_dir.is_dir():
        return []
    out = []
    for p in sorted(queue_dir.iterdir()):
        if not p.is_file() or not p.name.endswith(".json"):
            continue
        if p.name.endswith(_EXCLUDED_SUFFIXES):
            continue
        out.append(p)
    return out


def _classify(directive: dict, quarantine_dirs, home: str,
              lessons_dir) -> Optional[str]:
    """Return 'quarantined' | 'redundant' | None (keep). Never raises."""
    try:
        did = _resolve_directive_id(directive)
        if did and failed_quarantined(did, *quarantine_dirs):
            return "quarantined"
        out = declared_output(directive, home)
        if (out is not None and out.is_file()
                and not open_lessons_for(lessons_dir, out.name)):
            return "redundant"
    except Exception as e:
        log.warning("classify failed (kept): %s", e)
    return None


def run_pass(directives_root, home: Optional[str] = None,
             lessons_dir=None, quarantine_dirs=None, limit: int = 200) -> dict:
    """One retire pass over pending/ and proposed/. Returns stats. Never raises.

    limit bounds total moves per pass (backlog drains across cycles rather than
    in one burst -- promoter cadence is 60s, so a 200/pass bound clears even the
    worst observed backlog in a couple of minutes).
    """
    stats = {"scanned": 0, "retired": 0, "kept": 0, "errors": 0, "by_class": {}}
    try:
        root = Path(directives_root)
        home = home or str(root.parent)
        lessons_dir = lessons_dir or (root.parent / "lessons")
        if quarantine_dirs is None:
            quarantine_dirs = [root, _durable_quarantine_dir()]

        dest_root = None  # created lazily on first retirement
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        for queue in QUEUES:
            qdir = root / queue
            for f in _iter_directive_files(qdir):
                if stats["retired"] >= limit:
                    log.info("janitor limit %d reached; deferring rest", limit)
                    return stats
                stats["scanned"] += 1
                try:
                    directive = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    stats["errors"] += 1
                    continue  # never touch what we can't parse
                if not isinstance(directive, dict):
                    stats["errors"] += 1
                    continue
                cls = _classify(directive, quarantine_dirs, home, lessons_dir)
                if cls is None:
                    stats["kept"] += 1
                    continue
                label = f"{queue}_{cls}"
                try:
                    if dest_root is None:
                        dest_root = root / "retired" / ts
                    dest_dir = dest_root / label
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(dest_dir / f.name))
                    stats["retired"] += 1
                    stats["by_class"][label] = stats["by_class"].get(label, 0) + 1
                    log.info("retired %s/%s -> retired/%s/%s (%s)",
                             queue, f.name, ts, label, cls)
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("retire move failed for %s: %s", f.name, e)
    except Exception as e:  # belt: a janitor fault must never break the promoter
        stats["errors"] += 1
        log.warning("janitor pass aborted (fail-open): %s", e)
    return stats
