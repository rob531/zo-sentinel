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
     already exists AND its occupant is a stale TERMINAL squatter (.done/
     .failed), the fresh proposal is a duplicate of already-resolved work ->
     archive it as .duplicate (clears the collision deadlock WITHOUT
     rebuilding). Otherwise (non-terminal == possibly in-flight) log and skip
     -- never clobber a live pending.
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
    """Canonical directives root: the SAME absolute tower path goose_runner and the
    directive MCP hardcode (/home/workspace/zo_sentinel/directives), so promoted
    directives ALWAYS land where goose watches. Repo-local is only a CI/test fallback
    when the tower path is absent. Flipping this preference (was repo-local-first) is
    the durable fix for the recurring "proposals land where goose can't see them"
    funnel break -- the repo-local path drifts on every respawn/refresh/checkout."""
    if _TOWER_DIRECTIVES.exists():
        return _TOWER_DIRECTIVES
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
    # Malformed output_file guard (keep in sync with
    # build_completion.output_file_is_sane): a doubled leading prefix like
    # 'admin_admin_ui_suite.py' can never be produced and ghost-loops forever.
    of = (d.get("output_file") or "")
    if of:
        _stem = Path(str(of)).stem
        _parts = [p for p in _stem.split("_") if p]
        if len(_parts) >= 2 and _parts[0] == _parts[1]:
            return False, f"malformed output_file (doubled leading prefix): {_stem!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Directive identity
# ---------------------------------------------------------------------------


def _resolve_directive_id(d: dict) -> str:
    """Resolve a directive's identity the SAME way goose_runner.resolve_directive_id
    does: directive_id | id | key | task.

    THIS IS LOAD-BEARING. goose_runner names its terminal sentinels
    (<id>.done.json / <id>.failed.json) off this resolved id, and generator
    proposals (gen_<hash>_<task>.json) carry ONLY a `task` field -- no
    directive_id/id. If the promoter resolved identity differently (the old
    `directive_id or id` only) it computed "" for every generator directive,
    so it could never find their sentinels -> judged terminal squatters
    "non-terminal" -> the proposed->pending collision deadlock. Mirror the
    builder exactly so the two halves agree on identity.
    """
    return str(
        d.get("directive_id") or d.get("id") or d.get("key") or d.get("task") or ""
    ).strip()


# ---------------------------------------------------------------------------
# Service-unit fan-out (SOA atomic unit -- the multi-step ladder build)
# ---------------------------------------------------------------------------
#
# The ladder attribution audit's verdict: 559 single-file emissions, 16 load-
# bearing (2.9%) -- the FILE unit produced hollow, spineless inventory. The new
# unit is the SERVICE (logic/router/contract/service.toml under services/staged/
# <name>/), and it is built in MULTI STEPS by the ladder: the architect emits ONE
# general service-level directive; THIS pre-pass deterministically fans it out
# into N single-file directives (the builder's proven lane -- engine writes each
# in its own single-shot pass); the staged dir ACCRETES as each lands; the
# staged->active promotion gate is the completion condition (the whole unit must
# prove LIVE before it mounts). No orchestration state machine: the folder is the
# accumulator, the liveness contract is the join.
#
# A service directive is {"handler": "build_service", "service_name": ..,
# "description"/"spec": .., optional "prefix"/"tag"}. After fan-out the parent is
# renamed .expanded (auditable, never re-scanned). Kill: ZO_SERVICE_UNIT_EXPANSION=0.

# The architect has TWO emitters and they do not agree on field names. The
# converged path calls propose_directive and writes {"service_name","spec"}.
# The SALVAGE path -- which recovers a directive from the goose transcript when
# the model produced good content but never reached the tool call -- writes the
# generic directive shape {"task","description"} with handler="build_service".
# This function is the join. Before it existed the validator accepted only
# service_name/name, so every salvaged service directive was renamed .rejected:
# 207 files, 195 of them failing on nothing but the key spelling, while the
# builder logged "Total directives loaded: 0" for 471 consecutive cycles.
#
# "task" carries the directive name, not the service name, so it arrives
# prefixed (build_service_risk_tier_history). Strip the prefix or the service
# lands at services/staged/build_service_risk_tier_history/ -- the doubled-path
# squatter shape we have already paid for once.
_SERVICE_NAME_PREFIXES = ("build_service_", "build_")


def _service_name_of(d: dict) -> "tuple[str, str]":
    """Return (service_name, which_key_it_came_from). Empty name => unusable."""
    for key in ("service_name", "name", "task"):
        raw = str(d.get(key) or "").strip()
        if not raw:
            continue
        if key == "task":
            for pfx in _SERVICE_NAME_PREFIXES:
                if raw.startswith(pfx):
                    raw = raw[len(pfx):]
                    break
        raw = raw.strip("_")
        if raw:
            return raw, key
    return "", ""


def _expand_service_directives(proposed_dir: Path) -> int:
    if os.environ.get("ZO_SERVICE_UNIT_EXPANSION", "1") == "0":
        return 0
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.service_decomposer import decompose  # stdlib-only, like us
    except Exception as e:  # decomposer absent -> fan-out unavailable, visible
        log.warning("service fan-out unavailable (%s); build_service directives will be rejected", e)
        return 0
    expanded = 0
    for p in list(_iter_proposals(proposed_dir)):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("handler") != "build_service":
            continue
        name, name_src = _service_name_of(d)
        spec = str(d.get("spec") or d.get("description") or "").strip()
        if not name or len(spec) < 50:
            # Name the FAILING condition. "missing service_name OR spec<50" is an
            # ambiguous OR: it cost a day of reading 207 .rejected files to learn
            # that 195 of them failed only on the name, and had a perfectly good
            # 400-1400 char spec sitting in "description".
            why = []
            if not name:
                why.append("no service_name/name/task key")
            if len(spec) < 50:
                why.append("spec/description is %d chars (<50)" % len(spec))
            log.warning("build_service %s: %s -> .rejected", p.name, " AND ".join(why))
            try:
                os.replace(p, p.with_name(p.name + ".rejected"))
            except OSError:
                pass
            continue
        try:
            children = decompose(name, spec, str(d.get("prefix") or "/api"), str(d.get("tag") or ""))
        except ValueError as e:
            # FU-349 / #3415: reserved/malformed service name (e.g. a spec-parser
            # grabbed the concern-word "contract" as a service). Reject loudly;
            # never fan out a service that squats on a concern-word or a
            # load-bearing package.
            log.warning("build_service %s: %s -> .rejected", p.name, e)
            try:
                os.replace(p, p.with_name(p.name + ".rejected"))
            except OSError:
                pass
            continue
        for c in children:
            c["parent_service_directive"] = p.name
            stem = c["output_file"].replace("/", "_").replace(".", "_")
            child_path = proposed_dir / ("svc_%s.json" % stem)
            with open(child_path, "w", encoding="utf-8") as fh:
                json.dump(c, fh, indent=2)
        try:
            os.replace(p, p.with_name(p.name + ".expanded"))
        except OSError:
            pass
        expanded += 1
        log.info("service fan-out: %s -> %d single-file directives "
                 "(unit=services/staged/%s, name from %r)",
                 p.name, len(children), name, name_src)
    return expanded


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
        if ".bak" in name:
            # FU-011: a stale .bak copy is not a live proposal -- promoting or
            # counting it masks real queue emptiness. Janitor moves them to
            # directives_archive/bak_janitor/; never promote them.
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


def _terminal_sentinels(directives_root: Path, directive_id: str) -> Tuple[Path, Path]:
    """Return (done_sentinel, failed_sentinel) paths goose_runner writes.

    Matches goose_runner.mark_directive_completed:
        <directives_root>/<directive_id>.done.json   (built ok)
        <directives_root>/<directive_id>.failed.json (ghost give-up)
    """
    return (
        directives_root / f"{directive_id}.done.json",
        directives_root / f"{directive_id}.failed.json",
    )


def _durably_quarantined(directive_id: str) -> bool:
    """True if the DURABLE quarantine store (outside the git tree, survives
    `git clean` on daemon respawn) holds a <directive_id>.failed.json sentinel.

    goose_runner.is_goose_eligible has honored this store since the durable-
    quarantine fix, but the promoter's terminal checks did NOT -- so a durably-
    quarantined directive squatting in pending/ looked "possibly in-flight"
    (collision -> skip forever) and a RE-PROPOSAL of a durably-quarantined id
    passed the already-resolved check and re-entered pending/ as a fresh
    squatter. Both halves of the 2026-07-02 queue-saturation deadlock. This
    makes the promoter agree with goose on what "terminal" means.

    Env-repointable (ZO_DURABLE_QUARANTINE_DIR) for tests/CI, read fresh each
    call; stdlib-only; never raises.
    """
    if not directive_id:
        return False
    try:
        qdir = Path(os.environ.get(
            "ZO_DURABLE_QUARANTINE_DIR",
            "/home/workspace/zo_sentinel_state/quarantine"))
        return (qdir / f"{directive_id}.failed.json").exists()
    except Exception:
        return False


def _rename_duplicate(src: Path) -> None:
    """Move src aside to <name>.duplicate so an already-resolved re-proposal
    stops being scanned every cycle.

    .duplicate = valid but already-resolved (goose_runner has a .done/.failed
    sentinel for it). Distinct from .rejected (bad JSON / failed validation).

    BOUNDED: exactly ONE .duplicate per basename, clobbered on repeat. The
    architect re-proposes terminal directives every cycle (it sees them as
    "not built"), so suffix-bumping (.duplicate.1, .2, ...) would flood
    proposed/ with thousands of forensic copies within hours. One file is
    enough; the log line is the real forensic trail.
    """
    try:
        target = src.with_name(src.name + ".duplicate")
        os.replace(src, target)  # clobbers any prior .duplicate -> bounded to one
    except Exception as e:
        log.error("failed to rename duplicate %s: %s", src.name, e)


def _pending_is_terminal(pending_file: Path, directives_root: Path) -> bool:
    """True if the directive occupying a pending slot is already TERMINAL --
    it has a <directive_id>.done.json or <directive_id>.failed.json sentinel,
    so goose_runner will never rebuild it and it is a stale squatter that
    permanently blocks any new proposal sharing its filename.

    Resolves the pending file's OWN directive_id via _resolve_directive_id
    (directive_id|id|key|task) -- the SAME resolver goose_runner uses to name
    the sentinel. The old code keyed on directive_id/id only, which is "" for
    generator directives (task-keyed) -> always returned False -> the 9-stuck
    collision deadlock. Best-effort; returns False on any error so we NEVER
    supersede/clobber a pending file we cannot prove is terminal (a
    non-terminal pending file may be a legitimate in-flight build).
    """
    try:
        d = json.loads(pending_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("cannot read pending %s for terminal check: %s", pending_file.name, e)
        return False
    if not isinstance(d, dict):
        return False
    did = _resolve_directive_id(d)
    if not did:
        return False
    done, failed = _terminal_sentinels(directives_root, did)
    return done.exists() or failed.exists() or _durably_quarantined(did)


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

    # Terminal idempotency: goose_runner writes <directive_id>.done.json on a
    # successful build and <directive_id>.failed.json on a ghost give-up. If
    # EITHER sentinel exists, the architect has re-proposed an already-resolved
    # directive (typical when a circuit breaker stays tripped, or for the
    # MiniMax-unbuildable hard class that keeps re-failing). Archive the
    # proposal to .duplicate so we stop scanning it every cycle WITHOUT
    # re-promoting it -- re-promoting a .failed directive would re-run work the
    # builder already gave up on (ghost-rebuild thrash). Resolves the id via
    # _resolve_directive_id so it works for task-keyed generator directives.
    if directives_root is not None:
        directive_id = _resolve_directive_id(d)
        if directive_id:
            done_sentinel, failed_sentinel = _terminal_sentinels(
                directives_root, directive_id
            )
            done_exists = done_sentinel.exists()
            # Durable-aware: a durable quarantine is a .failed the same as the
            # in-repo sentinel -- re-promoting it would re-run work the builder
            # already gave up on (and re-create the pending squatter).
            failed_exists = failed_sentinel.exists() or _durably_quarantined(directive_id)
            if done_exists or failed_exists:
                log.info(
                    "skip already-resolved %s (directive_id=%s done=%s failed=%s)",
                    src.name, directive_id, done_exists, failed_exists,
                )
                counters.skipped += 1
                if not dry_run:
                    _rename_duplicate(src)
                return "already_resolved"

    dest = pending_dir / src.name
    if dest.exists():
        # Terminal collision: if the pending file occupying this slot is
        # already terminal (.done/.failed sentinel for ITS OWN resolved id),
        # goose_runner will never reconsume it -- it would otherwise block this
        # (distinct, newer) proposal FOREVER (the collision deadlock observed
        # 2026-06-14..16). The fresh proposal is a duplicate of already-resolved
        # work, so ARCHIVE it as .duplicate to clear the jam. We do NOT
        # supersede + re-promote: re-promoting a .failed directive re-runs work
        # the builder already gave up on (ghost-rebuild THRASH prior councils
        # forbade), and a stale ghost-.done is self-healed by
        # goose_runner.is_goose_eligible (which deletes the stale sentinel and
        # re-admits the directive) -- the promoter must not own that retry.
        # A pending file with NO terminal sentinel may be a legitimate in-flight
        # build, so we must NOT touch it -- skip as before.
        if directives_root is not None and _pending_is_terminal(dest, directives_root):
            log.info(
                "terminal squatter occupies %s slot -- archiving proposed duplicate (no rebuild)",
                src.name,
            )
            counters.skipped += 1
            if not dry_run:
                _rename_duplicate(src)
            return "terminal_dup_archived"
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


def _maybe_run_janitor(directives_root: Path) -> None:
    """Flag-gated skip=>retire pass (zo_sentinel.queue_janitor) at the top of
    each promotion cycle: retires dedup-redundant and durably-quarantined
    squatters from pending/ and proposed/ so collisions clear, proposed/ drains
    below the architect's depth cap, and novel directive emission never starves
    again (the 2026-07-02 queue-saturation deadlock).

    Default OFF: enable via env ZO_QUEUE_JANITOR=1 or the sentinel file
    <directives_root>/.queue_janitor_on containing "1" (restart-free, same
    pattern as the #1060 dedup flag). LAZY import + fail-open so this module
    stays stdlib-importable (hermetic CI gate) and a janitor fault can never
    break promotion.
    """
    try:
        from zo_sentinel import queue_janitor  # lazy: hermetic-import safe
        if not queue_janitor.enabled(directives_root):
            return
        stats = queue_janitor.run_pass(directives_root)
        if stats.get("retired") or stats.get("errors"):
            log.info("janitor: %s", stats)
    except Exception as e:
        log.warning("janitor unavailable/failed (fail-open): %s", e)


def run_once(
    proposed_dir: Path,
    pending_dir: Path,
    min_age_secs: int,
    max_per_cycle: int,
    dry_run: bool = False,
    directives_root: Path | None = None,
) -> dict:
    """One promotion pass. Returns the cycle counters as a dict.

    `directives_root` enables the terminal-sentinel checks inside _do_promote.
    If omitted, defaults to pending_dir.parent — the canonical layout has
    directives_root/{proposed,pending,*.done.json} as siblings.
    """
    _expand_service_directives(Path(proposed_dir))
    counters = PromotionCounters()

    if not proposed_dir.exists():
        log.info("proposed dir %s does not exist; nothing to do", proposed_dir)
        log.info(counters.summary_line())
        return counters.as_dict()

    if directives_root is None:
        directives_root = pending_dir.parent

    # Queue hygiene BEFORE scanning proposals, so a slot a squatter vacates
    # this cycle is promotable this same cycle. No-op unless enabled; skipped
    # in dry_run (a dry-run must not mutate the queues).
    if not dry_run:
        _maybe_run_janitor(directives_root)

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
            "and <directive_id>.failed.json sentinels. Defaults to "
            "pending-dir.parent. Setting this lets the promoter skip "
            "already-resolved duplicates instead of re-promoting them."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # Tripwire: the recurring funnel break is the promoter writing pending/ to a
    # folder goose_runner does NOT watch (silent scanned=0 for weeks). Resolve the
    # paths once at startup and ALERT loudly on any drift from goose's canonical dir.
    _goose_pending = _TOWER_DIRECTIVES / "pending"
    log.info("promoter paths: proposed=%s pending=%s", args.proposed_dir, args.pending_dir)
    if _TOWER_DIRECTIVES.exists() and Path(args.pending_dir).resolve() != _goose_pending.resolve():
        log.error("ALERT path-drift: pending-dir %s != goose pending %s -- promoted "
                  "directives will be INVISIBLE to goose_runner (this is the recurring "
                  "funnel break). Fix the launcher's --pending-dir or the path constant.",
                  args.pending_dir, _goose_pending)

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
