#!/usr/bin/env python3
"""
proposed_to_pending_promoter.py -- promote validated directives from
directives/proposed/ to directives/pending/.

Implements ROLLOUT.md Step 4b. Sits downstream of the Phase 0b dormant daemon
(sentinel_directive_generator_goose.py) which produces proposals into
directives/proposed/, and upstream of goose_runner.py which watches
directives/pending/.

WHY THIS EXISTS:
  - PR #1 (Phase 0b) wired the directive_architect to write *proposals* into
    a sandbox dir (proposed/) that goose_runner does NOT watch. That was
    deliberate: it lets the new LLM-driven generator run without risking the
    active build chain.
  - The downstream half — moving validated proposals into pending/ where
    goose_runner picks them up — was deferred. Without it, proposed/ fills
    up to its depth cap and the Phase 0b daemon starts logging
    "proposed/ depth 40 >= cap 40; skipping cycle" forever.
  - This module is that downstream half.

TWO MODES:
  - Daemon (default): poll loop, supervisord-managed. Dormant until the
    supervisord block in docs/PROMOTER.md is installed.
  - One-shot (--once): single promotion pass and exit. Useful for an
    immediate manual unblock against an existing backlog.

PROMOTION RULES (per ROLLOUT.md Step 4b):
  1. TTL guard: only consider files whose mtime is older than
     --min-age-secs (default 60). Lets a human skim very-recent proposals.
  2. Flag check: if <basename>.skip exists alongside, hold the file out.
  3. Validate: shape-check the directive dict (mirrors
     zo_sentinel.mcp_servers.directive_mcp._validate / sentinel_directive_
     generator.validate_directive). Invalid files are renamed to .rejected
     so they do not get reconsidered every cycle.
  4. Atomic move: os.replace(src, pending/<basename>). If the destination
     already exists AND its occupant is a stale terminal squatter, supersede
     it; otherwise log and skip (do NOT overwrite a live/in-flight pending).
  5. Per-cycle cap: --max-per-cycle (default 10) so a deep backlog doesn't
     all land in pending at once.

VALIDATOR IMPORT NOTE:
  We tried to reuse zo_sentinel.mcp_servers.directive_mcp._validate and the
  legacy sentinel_directive_generator.validate_directive, but BOTH have
  module-level side effects that fail outside the tower:
    - directive_mcp imports mcp.server.fastmcp (sys.exit(2) on miss)
      and mkdir()s a hardcoded /home/workspace path
    - sentinel_directive_generator imports requests and other modules and
      mkdir()s the same hardcoded path
  Rather than fight the import, this module *inlines* the same validation
  semantics. The rule set is small and stable. If it ever drifts from the
  canonical validator, fix it here — this is the only validation gate
  between proposed/ and pending/, and the canonical writers (the MCP server
  and legacy generator) already validate before writing into proposed/
  anyway, so we are mostly a defence-in-depth check.

DEPENDENCIES:
  Stdlib only. No requests, no mcp, no third-party imports. This module
  must import cleanly on Windows (where Robin runs tests) and on the
  tower (where the daemon runs).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil  # noqa: F401  -- kept for parity with the spec's allowed list
import sys
import time
from pathlib import Path
from typing import Tuple

# ---------------------------------------------------------------------------
# Path resolution
#
# Default to repo-relative paths so the module is portable to Windows / CI.
# Fall back to the tower-side absolute path layout for supervisord compat,
# matching the pattern used by refresh_schema_doc.py and friends.
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
# zo_sentinel/promoters/proposed_to_pending_promoter.py -> repo root
REPO_ROOT = _THIS_FILE.parents[2]

_REPO_DIRECTIVES = REPO_ROOT / "directives"
_TOWER_DIRECTIVES = Path("/home/workspace/zo_sentinel/directives")


def _default_directives_root() -> Path:
    """Prefer the repo-local directives/ dir; fall back to tower path."""
    if _REPO_DIRECTIVES.exists():
        return _REPO_DIRECTIVES
    if _TOWER_DIRECTIVES.exists():
        return _TOWER_DIRECTIVES
    # Neither exists yet (fresh checkout, no tower) — pick repo-local
    # so tests / dry-runs work; the caller can override with --proposed-dir.
    return _REPO_DIRECTIVES


DEFAULT_PROPOSED_DIR = _default_directives_root() / "proposed"
DEFAULT_PENDING_DIR = _default_directives_root() / "pending"

DEFAULT_LOG_PATH = Path("/home/workspace/logs/proposed_to_pending_promoter.log")

# ---------------------------------------------------------------------------
# Logging setup
#
# Same pattern as sentinel_directive_generator_goose.py: try the canonical
# tower log path; if it isn't writable (Windows, CI, fresh checkout), fall
# back to stderr. We MUST NOT raise from import.
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [promoter] %(levelname)s: %(message)s"


def _setup_logging(log_path: Path = DEFAULT_LOG_PATH) -> logging.Logger:
    handlers: list[logging.Handler] = []
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:
        # Not writable -> stderr only. Silent because we cannot log yet.
        pass
    handlers.append(logging.StreamHandler(sys.stderr))

    # configure on the named logger directly so test imports don't clobber
    # the root logger's handlers (pytest sets them up too).
    logger = logging.getLogger("promoter")
    logger.setLevel(logging.INFO)
    # Clear any handlers we previously added so re-import in tests is idempotent.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    for h in handlers:
        h.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(h)
    logger.propagate = False
    return logger


log = _setup_logging()


# ---------------------------------------------------------------------------
# Validator (inlined; mirrors directive_mcp._validate semantics)
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"task", "handler", "output_file", "description"}
VALID_HANDLERS = {"generate_file", "write_raw", "run_script"}
VALID_COMPLEXITY = {"low", "medium", "high"}


def _validate(d: dict) -> Tuple[bool, str]:
    """Mirror directive_mcp._validate's shape checks.

    We deliberately do NOT re-check ALREADY_BUILT / PROTECTED_FILES here:
    those lists live on the tower in sentinel_directive_generator.py and
    are checked at write-time by the canonical producers. A directive that
    landed in proposed/ has already passed that gate. This promoter's job
    is to defend against bit-rot or hand-edited proposals — basic shape
    integrity.
    """
    if not isinstance(d, dict):
        return False, "not a dict"
    missing = REQUIRED_FIELDS - d.keys()
    if missing:
        return False, f"missing fields: {sorted(missing)}"
    if d.get("handler") not in VALID_HANDLERS:
        return False, f"invalid handler: {d.get('handler')!r}"
    if d.get("complexity") and d["complexity"] not in VALID_COMPLEXITY:
        return False, f"invalid complexity: {d.get('complexity')!r}"
    if len(d.get("description", "") or "") < 50:
        return False, "description too short (<50 chars)"
    return True, "ok"


# ---------------------------------------------------------------------------
# Promotion pass
# ---------------------------------------------------------------------------


class PromotionCounters:
    """Per-cycle counters. Mutable struct, dict-flavored."""

    __slots__ = ("scanned", "eligible", "promoted", "rejected", "skipped", "too_young")

    def __init__(self) -> None:
        self.scanned = 0
        self.eligible = 0
        self.promoted = 0
        self.rejected = 0
        self.skipped = 0
        self.too_young = 0

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "eligible": self.eligible,
            "promoted": self.promoted,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "too_young": self.too_young,
        }

    def summary_line(self) -> str:
        return (
            f"cycle: scanned={self.scanned} eligible={self.eligible} "
            f"promoted={self.promoted} rejected={self.rejected} "
            f"skipped={self.skipped}"
        )


def _iter_proposals(proposed_dir: Path):
    """Yield candidate *.json files in proposed_dir, sorted by mtime asc.

    Excludes .done.json / .failed.json / .rejected files. Sorting oldest-
    first means a long backlog drains in age order, FIFO-ish.
    """
    if not proposed_dir.exists():
        return
    candidates = []
    for p in proposed_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if not name.endswith(".json"):
            continue
        if name.endswith(".done.json") or name.endswith(".failed.json"):
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime)
    for p in candidates:
        yield p


def _is_too_young(path: Path, min_age_secs: int, now: float | None = None) -> bool:
    """File is too young if its mtime is newer than (now - min_age_secs).

    Edge: a file whose mtime is exactly at the threshold is NOT too young
    (>= boundary inclusive on the eligible side). This matches the spec's
    edge-case test.
    """
    now = time.time() if now is None else now
    age = now - path.stat().st_mtime
    return age < min_age_secs


def _skip_marker_path(p: Path) -> Path:
    """Return the .skip marker path sibling for a proposal file.

    Convention: gen_xxx.json -> gen_xxx.json.skip (basename + .skip).
    """
    return p.with_name(p.name + ".skip")


def _done_sentinel_path(directives_root: Path, directive_id: str) -> Path:
    """Where goose_runner writes its 'already-built' sentinel.

    Matches goose_runner.mark_directive_completed:
        /home/workspace/zo_sentinel/directives/<directive_id>.done.json
    """
    return directives_root / f"{directive_id}.done.json"


def _rename_duplicate(src: Path) -> None:
    """Rename src to src.name + '.duplicate' so it isn't reconsidered.

    Same shape as _rename_rejected, different suffix to distinguish:
    .rejected = bad JSON / failed validation
    .duplicate = valid but already-built (goose_runner has the .done.json sentinel)
    """
    try:
        target = src.with_name(src.name + ".duplicate")
        i = 1
        while target.exists():
            target = src.with_name(f"{src.name}.duplicate.{i}")
            i += 1
        os.replace(src, target)
    except Exception as e:
        log.error("failed to rename duplicate %s: %s", src.name, e)


def _pending_is_terminal(pending_file: Path, directives_root: Path) -> bool:
    """True if the directive occupying a pending slot is already TERMINAL --
    it has a <directive_id>.done.json or <directive_id>.failed.json sentinel,
    so goose_runner will never rebuild it and it is a stale squatter that
    permanently blocks any new proposal sharing its filename.

    Reads the pending file to recover its OWN directive_id (NOT the gen_<hash>
    filename -- sentinels are keyed by the bare directive_id, the classic
    key-mismatch trap). Best-effort; returns False on any error so we NEVER
    supersede a pending file we cannot prove is terminal (a non-terminal
    pending file may be a legitimate in-flight/queued build -- do NOT clobber).
    """
    try:
        d = json.loads(pending_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("cannot read pending %s for terminal check: %s", pending_file.name, e)
        return False
    if not isinstance(d, dict):
        return False
    did = str(d.get("directive_id") or d.get("id") or "").strip()
    if not did:
        return False
    done = directives_root / f"{did}.done.json"
    failed = directives_root / f"{did}.failed.json"
    return done.exists() or failed.exists()


def _archive_superseded(pending_file: Path) -> None:
    """Move a stale terminal pending squatter aside (-> .superseded) so the
    fresh proposal can take its slot, without losing the old file for forensics.
    """
    try:
        target = pending_file.with_name(pending_file.name + ".superseded")
        i = 1
        while target.exists():
            target = pending_file.with_name(f"{pending_file.name}.superseded.{i}")
            i += 1
        os.replace(pending_file, target)
    except Exception as e:
        log.error("failed to archive superseded %s: %s", pending_file.name, e)


def _do_promote(
    src: Path,
    pending_dir: Path,
    dry_run: bool,
    counters: PromotionCounters,
    directives_root: Path | None = None,
) -> str:
    """Validate src and move it into pending_dir. Returns outcome string."""
    try:
        text = src.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("read failed %s: %s -> reject", src.name, e)
        counters.rejected += 1
        if not dry_run:
            _rename_rejected(src)
        return "rejected"

    try:
        d = json.loads(text)
    except Exception as e:
        log.warning("invalid JSON %s: %s -> reject", src.name, e)
        counters.rejected += 1
        if not dry_run:
            _rename_rejected(src)
        return "rejected"

    ok, reason = _validate(d)
    if not ok:
        log.warning("validation failed %s: %s -> reject", src.name, reason)
        counters.rejected += 1
        if not dry_run:
            _rename_rejected(src)
        return "rejected"

    # Done-sentinel idempotency: goose_runner writes
    # <directives_root>/<directive_id>.done.json when a directive completes.
    # If the sentinel exists, the architect has re-proposed an already-built
    # directive (typical when a circuit breaker stays tripped and the
    # architect keeps re-suggesting "investigate" actions). Move the
    # proposal to .duplicate so we stop scanning it every cycle without
    # losing it for forensics.
    if directives_root is not None:
        directive_id = d.get("directive_id") or d.get("id") or ""
        if directive_id:
            sentinel = _done_sentinel_path(directives_root, str(directive_id))
            if sentinel.exists():
                log.info(
                    "skip already-built %s (directive_id=%s sentinel=%s)",
                    src.name, directive_id, sentinel,
                )
                counters.skipped += 1
                if not dry_run:
                    _rename_duplicate(src)
                return "already_built"

    dest = pending_dir / src.name
    if dest.exists():
        # Terminal-supersede: if the pending file occupying this slot is
        # already terminal (.done/.failed sentinel for ITS OWN directive_id),
        # it is a stale squatter goose_runner will never reconsume -- it would
        # otherwise block this (distinct, newer) proposal FOREVER (the 5-stuck
        # collision deadlock observed 2026-06-14). Archive it and take the slot.
        # A pending file with NO terminal sentinel may be a legitimate in-flight
        # build, so we must NOT clobber it -- skip as before.
        if directives_root is not None and _pending_is_terminal(dest, directives_root):
            log.info("superseding stale terminal pending squatter %s", src.name)
            if dry_run:
                log.info("DRY-RUN would supersede %s -> %s", src.name, dest)
                counters.promoted += 1
                return "promoted"
            _archive_superseded(dest)
            try:
                pending_dir.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)
            except Exception as e:
                log.error("supersede os.replace failed for %s: %s", src.name, e)
                counters.skipped += 1
                return "move_failed"
            log.info("PROMOTED (superseded stale squatter) %s -> %s", src.name, dest)
            counters.promoted += 1
            return "promoted"
        log.warning("destination collision %s already in pending (non-terminal); skip", src.name)
        counters.skipped += 1
        return "collision"

    if dry_run:
        log.info("DRY-RUN would promote %s -> %s", src.name, dest)
        counters.promoted += 1
        return "promoted"

    try:
        pending_dir.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
    except Exception as e:
        log.error("os.replace failed for %s: %s", src.name, e)
        # Do NOT count as promoted; leave the file in place for next pass.
        counters.skipped += 1
        return "move_failed"

    log.info("PROMOTED %s -> %s", src.name, dest)
    counters.promoted += 1
    return "promoted"


def _rename_rejected(src: Path) -> None:
    """Rename src to src.name + '.rejected' so it isn't reconsidered."""
    try:
        target = src.with_name(src.name + ".rejected")
        # If a previous rejection lingered, do not clobber it — bump suffix.
        i = 1
        while target.exists():
            target = src.with_name(f"{src.name}.rejected.{i}")
            i += 1
        os.replace(src, target)
    except Exception as e:
        log.error("failed to rename rejected %s: %s", src.name, e)


def run_once(
    proposed_dir: Path,
    pending_dir: Path,
    min_age_secs: int,
    max_per_cycle: int,
    dry_run: bool = False,
    directives_root: Path | None = None,
) -> dict:
    """One promotion pass. Returns the cycle counters as a dict.

    `directives_root` enables the done-sentinel check inside _do_promote.
    If omitted, defaults to pending_dir.parent — the canonical layout has
    directives_root/{proposed,pending,*.done.json} as siblings.
    """
    counters = PromotionCounters()

    if not proposed_dir.exists():
        log.info("proposed dir %s does not exist; nothing to do", proposed_dir)
        log.info(counters.summary_line())
        return counters.as_dict()

    if directives_root is None:
        directives_root = pending_dir.parent

    promoted_or_attempted = 0
    for src in _iter_proposals(proposed_dir):
        counters.scanned += 1

        # Cap: count anything that *would have moved* (promote/reject) toward
        # the per-cycle cap. Validation rejects still count because they
        # represent a rename action.
        if promoted_or_attempted >= max_per_cycle:
            counters.skipped += 1
            log.info("cap reached (%d); deferring %s", max_per_cycle, src.name)
            continue

        if _skip_marker_path(src).exists():
            log.info("skip marker present for %s", src.name)
            counters.skipped += 1
            continue

        if _is_too_young(src, min_age_secs):
            counters.too_young += 1
            continue

        counters.eligible += 1
        promoted_or_attempted += 1
        _do_promote(src, pending_dir, dry_run, counters, directives_root=directives_root)

    log.info(counters.summary_line())
    return counters.as_dict()


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------


def run_daemon(
    proposed_dir: Path,
    pending_dir: Path,
    poll_secs: int,
    min_age_secs: int,
    max_per_cycle: int,
    heartbeat_secs: int = 60,
    sleep_func=time.sleep,
    directives_root: Path | None = None,
) -> None:
    """Forever loop: run_once, then sleep poll_secs.

    sleep_func is parameterised so tests can break out of the loop after
    one cycle. The heartbeat is a simple log line at <= HEARTBEAT_SECS
    cadence so supervisord-style watchers see liveness.
    """
    log.info(
        "promoter daemon starting: proposed=%s pending=%s poll=%ds min_age=%ds cap=%d",
        proposed_dir, pending_dir, poll_secs, min_age_secs, max_per_cycle,
    )
    last_heartbeat = 0.0
    while True:
        try:
            run_once(
                proposed_dir,
                pending_dir,
                min_age_secs,
                max_per_cycle,
                directives_root=directives_root,
            )
        except Exception as e:
            log.exception("cycle error: %s", e)

        now = time.time()
        if now - last_heartbeat >= heartbeat_secs:
            log.info("heartbeat: alive")
            last_heartbeat = now

        try:
            sleep_func(poll_secs)
        except StopIteration:
            # tests use a sleep stub that raises StopIteration to break out.
            log.info("daemon stop requested")
            return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("env %s=%r not int; using default %d", name, raw, default)
        return default


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="proposed_to_pending_promoter",
        description="Promote validated directives from proposed/ to pending/.",
    )
    p.add_argument(
        "--proposed-dir",
        type=Path,
        default=DEFAULT_PROPOSED_DIR,
        help=f"Source directory (default: {DEFAULT_PROPOSED_DIR})",
    )
    p.add_argument(
        "--pending-dir",
        type=Path,
        default=DEFAULT_PENDING_DIR,
        help=f"Destination directory (default: {DEFAULT_PENDING_DIR})",
    )
    p.add_argument(
        "--poll-secs",
        type=int,
        default=_int_env("PROMOTER_POLL_SECS", 60),
        help="Daemon poll interval (default: 60, env PROMOTER_POLL_SECS)",
    )
    p.add_argument(
        "--min-age-secs",
        type=int,
        default=_int_env("PROMOTER_MIN_AGE_SECS", 60),
        help="Minimum proposal age before eligible (default: 60, env PROMOTER_MIN_AGE_SECS)",
    )
    p.add_argument(
        "--max-per-cycle",
        type=int,
        default=_int_env("PROMOTER_MAX_PER_CYCLE", 10),
        help="Max files promoted per cycle (default: 10, env PROMOTER_MAX_PER_CYCLE)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run one pass and exit (otherwise: daemon loop).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen; do not move/rename anything.",
    )
    p.add_argument(
        "--directives-root",
        type=Path,
        default=None,
        help=(
            "Root directory where goose_runner writes <directive_id>.done.json "
            "sentinels. Defaults to pending-dir.parent. Setting this lets the "
            "promoter skip already-built duplicates instead of re-promoting them."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.once:
        run_once(
            args.proposed_dir,
            args.pending_dir,
            args.min_age_secs,
            args.max_per_cycle,
            dry_run=args.dry_run,
            directives_root=args.directives_root,
        )
        return 0

    if args.dry_run:
        # --dry-run only makes sense with --once; refuse for daemon mode
        # because we'd be lying about doing work forever.
        log.error("--dry-run requires --once")
        return 2

    run_daemon(
        args.proposed_dir,
        args.pending_dir,
        poll_secs=args.poll_secs,
        min_age_secs=args.min_age_secs,
        max_per_cycle=args.max_per_cycle,
        directives_root=args.directives_root,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
