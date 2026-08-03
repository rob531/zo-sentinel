#!/usr/bin/env python3
"""
gate_quality_state.py -- shared state for Gate 8 circuit breaker.

Single source of truth for two things:
    1. Current breaker state: closed / tripped / half-open
    2. Per-file retry counts (how many times has X been rebuilt and failed?)

State file is /home/workspace/zo_sentinel/gate_quality_state.json.
Human-readable, hand-editable, small enough that concurrent writes are
protected by a simple file lock via fcntl. Both Gate 8 (writer) and
directive_knowledge_sources.py (reader) use this module -- NO direct
JSON fiddling from other code.

Breaker semantics:
    closed    -- normal operation; generator may propose rebuilds
    tripped   -- too many failures; generator must NOT propose rebuilds
                 of any failed file. Resets require human action via
                 reset_breaker.py tool.
    half-open -- human-initiated recovery state; generator may propose
                 one batch of rebuilds. Gate 8 auto-closes on next clean
                 cohort >= MIN_COHORT_SIZE.

Trip rules (evaluated by Gate 8 at end of run):
    - single cohort with size >= MIN_COHORT_SIZE AND fail rate >= 40%
    - OR 3 consecutive cohorts with size >= MIN_COHORT_SIZE AND fail rate >= 30%

Per-file retry cap:
    A file that has failed Gate 8 MAX_REBUILDS times (default 3) is
    eligible for quarantine. Counter increments ONLY when Gate 8 observes
    a fresh build of that file (i.e., new cohort entry in mesh_memory)
    that fails. Repeated failures of the SAME build don't inflate.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    import fcntl  # POSIX-only (the live host). Absent on Windows / CI dev boxes.
except ImportError:  # pragma: no cover -- exercised only off-host
    fcntl = None

# State file location. Defaults to the live host path; overridable via env so
# the module imports and tests run hermetically off-host (same env-parametrization
# pattern as ZO_WRITE_SERVICE / GATE_ERRORS_DB elsewhere). Resolved lazily (see
# _resolve_state_file) so a test can repoint it after import.
_DEFAULT_STATE_FILE = "/home/workspace/zo_sentinel/gate_quality_state.json"
STATE_FILE = Path(os.environ.get("GATE_QUALITY_STATE_FILE", _DEFAULT_STATE_FILE))


def _resolve_state_file() -> Path:
    """Re-read the env each call so a test can repoint the state file without
    re-importing. Falls back to the module-level STATE_FILE."""
    return Path(os.environ.get("GATE_QUALITY_STATE_FILE", str(STATE_FILE)))

# Tunables (also documented in gate_8_new_module.py for operator reference)
BREAKER_FAIL_THRESHOLD_SINGLE    = 0.40   # 40% in one cohort trips
BREAKER_FAIL_THRESHOLD_RUNNING   = 0.30   # 30% * 3 consecutive also trips
BREAKER_RUNNING_WINDOW           = 3      # consecutive cohorts to watch
MIN_COHORT_SIZE                  = 4      # cohorts smaller than this don't count
MAX_REBUILDS                     = 3      # per-file retry budget

# Auto-recovery for STALE trips. Once tripped, the breaker starves its own
# recovery signal: the generator stops proposing rebuilds, so no fresh cohorts
# arrive, so a "clean cohort" can never auto-close it -- it waits forever for a
# human. This steps a stale trip (tripped > AUTO_RECOVER_AFTER_SECS ago with NO
# failing cohort since) to half-open, which then lets ONE batch prove green
# (record_cohort auto-closes) or re-trip. 0 disables (pure manual reset).
# Env-overridable for ops tuning. Default 6h.
AUTO_RECOVER_AFTER_SECS          = int(os.environ.get("BREAKER_AUTO_RECOVER_SECS", 6 * 3600))

_DEFAULT_STATE = {
    "state": "closed",                 # closed | tripped | half-open
    "state_changed_at": None,          # ISO timestamp of last transition
    "state_changed_reason": None,      # human-readable note
    "recent_cohorts": [],              # list of {id, size, fail_rate, observed_at}
    "file_retries": {},                # {filename: {attempts: N, last_failed_at: iso, last_error: str}}
    "quarantined": {},                 # {filename: {quarantined_at: iso, reason: str, attempts_when_quarantined: N}}
    "retired": {},                     # {filename: {retired_at: iso, reason: str}} -- permanently excluded from rebuild proposals
    "notes": [],                       # human-appendable operator notes
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    """ISO8601 -> epoch seconds, or None if unparseable/missing."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


class _LockedStateFile:
    """Context manager for read-modify-write with fcntl advisory lock.
    Creates the file with defaults if missing."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path if path is not None else _resolve_state_file()
        self.fh = None
        self.data = None

    def __enter__(self):
        # Ensure parent exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open for read+write; create if missing
        if not self.path.exists():
            self.path.write_text(json.dumps(_DEFAULT_STATE, indent=2))
        self.fh = open(self.path, "r+")
        # Exclusive lock, block until acquired (bounded by 5s via timeout loop).
        # fcntl is POSIX-only; off-host (Windows/CI) it's None and we run lockless
        # -- safe there because callers are single-process.
        if fcntl is not None:
            acquired = False
            for _ in range(50):
                try:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(0.1)
            if not acquired:
                self.fh.close()
                raise RuntimeError(f"Could not acquire lock on {self.path} in 5s")
        try:
            raw = self.fh.read() or ""
            self.data = json.loads(raw) if raw.strip() else dict(_DEFAULT_STATE)
        except json.JSONDecodeError:
            # Corrupted; back it up and reset
            backup = self.path.with_suffix(f".bak.{int(time.time())}.corrupt.json")
            self.path.rename(backup)
            self.data = dict(_DEFAULT_STATE)
        # Fill any missing top-level keys from defaults (forward-compatible)
        for k, v in _DEFAULT_STATE.items():
            self.data.setdefault(k, v if not isinstance(v, (list, dict)) else (list(v) if isinstance(v, list) else dict(v)))
        return self.data

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                # Write back
                self.fh.seek(0)
                self.fh.truncate()
                self.fh.write(json.dumps(self.data, indent=2))
                self.fh.flush()
                os.fsync(self.fh.fileno())
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            self.fh.close()


# ── Public read API (for directive_knowledge_sources) ─────────────────────────

def snapshot() -> dict:
    """Return a dict copy of current state. No lock contention for readers --
    this is a quick read+close."""
    with _LockedStateFile() as data:
        return json.loads(json.dumps(data))  # deep copy via JSON


def get_breaker_state() -> str:
    maybe_auto_recover()
    s = snapshot().get("state", "closed")
    # CHAIRMAN 2026-06-20: the binary "tripped" latch is non-blocking now (see
    # may_rebuild). Report a stale trip as "half-open" so no consumer (the
    # architect context / read_gate_quality_state) treats it as a hard generation
    # block. The raw "tripped" is still recorded in the state file for forensics.
    return "half-open" if s == "tripped" else s


def is_quarantined(filename: str) -> bool:
    drop_nonidentifying_keys()
    return filename in snapshot().get("quarantined", {})


def is_retired(filename: str) -> bool:
    return filename in snapshot().get("retired", {})


def retry_count(filename: str) -> int:
    q = snapshot().get("file_retries", {})
    return q.get(filename, {}).get("attempts", 0)


def may_rebuild(filename: str) -> tuple[bool, str]:
    """Primary query used by directive_knowledge_sources. Returns
    (ok, reason). Reason is always populated so the generator prompt can
    include it."""
    maybe_auto_recover()
    # Converge away keys that cannot identify an artifact BEFORE answering.
    # Without this a poisoned pre-fix entry keeps returning False for every
    # service-unit directive and the architect self-censors on it.
    drop_nonidentifying_keys()
    snap = snapshot()
    if filename in snap.get("retired", {}):
        r = snap["retired"][filename]
        return False, f"retired at {r.get('retired_at')}: {r.get('reason')}"
    # CHAIRMAN 2026-06-20: binary GATE LATCH blocklisting DISABLED. The global
    # "tripped" latch starved the directive generator -- it blocks ALL proposals
    # (not just the failing file) and the architect self-censors on it, requiring a
    # manual reset_breaker.py each time. We are rearchitecting to lessons-shaped soft
    # signals; until then the breaker RECORDS (record_cohort + per-file quarantine
    # below stay intact for observability) but does NOT block. To re-enable, uncomment:
    # if snap["state"] == "tripped":
    #     return False, "circuit breaker tripped -- manual reset required"
    if filename in snap.get("quarantined", {}):
        q = snap["quarantined"][filename]
        return False, f"quarantined at {q.get('quarantined_at')}: {q.get('reason')}"
    attempts = snap.get("file_retries", {}).get(filename, {}).get("attempts", 0)
    if attempts >= MAX_REBUILDS:
        return False, f"retry budget exhausted ({attempts}/{MAX_REBUILDS})"
    return True, "ok"


# ── Public write API (for Gate 8) ─────────────────────────────────────────────

def record_cohort(cohort_id: str, size: int, fail_rate: float) -> dict:
    """Append a cohort observation and evaluate breaker rules.
    Returns the post-evaluation state dict."""
    with _LockedStateFile() as data:
        data["recent_cohorts"].append({
            "id": cohort_id,
            "size": size,
            "fail_rate": round(fail_rate, 3),
            "observed_at": _now(),
        })
        # Keep only last 20 cohorts
        data["recent_cohorts"] = data["recent_cohorts"][-20:]

        # Evaluate trip rules (only if currently closed; tripped stays
        # tripped until manual reset; half-open transitions handled below)
        if data["state"] == "closed":
            # Rule 1: single cohort above single-threshold
            if size >= MIN_COHORT_SIZE and fail_rate >= BREAKER_FAIL_THRESHOLD_SINGLE:
                data["state"] = "tripped"
                data["state_changed_at"] = _now()
                data["state_changed_reason"] = (
                    f"single cohort trip: {cohort_id} size={size} "
                    f"fail_rate={fail_rate:.2%} >= {BREAKER_FAIL_THRESHOLD_SINGLE:.0%}"
                )
            else:
                # Rule 2: N consecutive cohorts above running-threshold
                recent = [c for c in data["recent_cohorts"][-BREAKER_RUNNING_WINDOW:]
                          if c["size"] >= MIN_COHORT_SIZE]
                if len(recent) >= BREAKER_RUNNING_WINDOW and all(
                    c["fail_rate"] >= BREAKER_FAIL_THRESHOLD_RUNNING for c in recent
                ):
                    data["state"] = "tripped"
                    data["state_changed_at"] = _now()
                    data["state_changed_reason"] = (
                        f"running-window trip: {BREAKER_RUNNING_WINDOW} consecutive "
                        f"cohorts all >= {BREAKER_FAIL_THRESHOLD_RUNNING:.0%}"
                    )

        elif data["state"] == "half-open":
            # Half-open closes on first clean enough cohort (size>=min, fail=0)
            if size >= MIN_COHORT_SIZE and fail_rate == 0:
                data["state"] = "closed"
                data["state_changed_at"] = _now()
                data["state_changed_reason"] = (
                    f"half-open -> closed: clean cohort {cohort_id} size={size}"
                )
            elif size >= MIN_COHORT_SIZE and fail_rate >= BREAKER_FAIL_THRESHOLD_SINGLE:
                # Re-trip immediately if half-open produces another bad cohort
                data["state"] = "tripped"
                data["state_changed_at"] = _now()
                data["state_changed_reason"] = (
                    f"half-open re-trip: {cohort_id} size={size} "
                    f"fail_rate={fail_rate:.2%}"
                )

        return json.loads(json.dumps(data))


def record_failure(filename: str, error: str, cohort_id: str) -> dict:
    """Increment per-file retry counter. Caller (Gate 8) is responsible
    for calling this ONCE per new build observation, not per check.
    Returns updated file_retries entry."""
    with _LockedStateFile() as data:
        entry = data["file_retries"].setdefault(filename, {
            "attempts": 0,
            "last_failed_at": None,
            "last_error": None,
            "cohorts": [],
        })
        entry["attempts"] += 1
        entry["last_failed_at"] = _now()
        entry["last_error"] = error[:200]
        if cohort_id not in entry.get("cohorts", []):
            entry.setdefault("cohorts", []).append(cohort_id)
            entry["cohorts"] = entry["cohorts"][-10:]
        return dict(entry)


def clear_retry(filename: str) -> bool:
    """Reset a file's retry counter. Called by Gate 8 when it observes a
    fresh successful build of a previously-failing file."""
    with _LockedStateFile() as data:
        if filename in data.get("file_retries", {}):
            del data["file_retries"][filename]
            return True
        return False


def record_quarantine(filename: str, reason: str, attempts: int) -> dict:
    """Mark a file as quarantined. Safe to call multiple times; first
    call's timestamp is preserved."""
    with _LockedStateFile() as data:
        q = data.setdefault("quarantined", {})
        if filename not in q:
            q[filename] = {
                "quarantined_at": _now(),
                "reason": reason,
                "attempts_when_quarantined": attempts,
            }
        return dict(q[filename])


def release_quarantine(filename: str, note: str = "") -> bool:
    """Used by reset_breaker.py or manual operator action. Does NOT move
    the actual file out of quarantine/ -- that's a separate human step.
    Clears the state entry and resets retry counter so the next build is
    treated as fresh."""
    with _LockedStateFile() as data:
        changed = False
        if filename in data.get("quarantined", {}):
            del data["quarantined"][filename]
            changed = True
        if filename in data.get("file_retries", {}):
            del data["file_retries"][filename]
            changed = True
        if note:
            data.setdefault("notes", []).append({
                "at": _now(),
                "action": "release_quarantine",
                "filename": filename,
                "note": note,
            })
            data["notes"] = data["notes"][-50:]
        return changed


def retire(filename: str, reason: str = "") -> dict:
    """Permanently exclude a file from rebuild proposals.

    Unlike quarantine (a recoverable 'failed too much, hold off' state that the
    generator still churns on), retirement says 'stop proposing this at all' --
    for dead targets: one-shot patchers that already ran, or check/test scripts
    now owned by the GitHub CI gates. Retiring also clears any quarantine/retry
    bookkeeping for the file (it's no longer in the build loop). Idempotent:
    the first call's timestamp/reason is preserved. Returns the retired entry."""
    with _LockedStateFile() as data:
        retired = data.setdefault("retired", {})
        if filename not in retired:
            retired[filename] = {"retired_at": _now(), "reason": reason or "no reason given"}
            # Drop it from the active build-loop accounting -- it's gone now.
            data.get("quarantined", {}).pop(filename, None)
            data.get("file_retries", {}).pop(filename, None)
            data.setdefault("notes", []).append({
                "at": _now(),
                "action": "retire",
                "filename": filename,
                "reason": reason or "no reason given",
            })
            data["notes"] = data["notes"][-50:]
        return dict(retired[filename])


def unretire(filename: str, note: str = "") -> bool:
    """Reverse a retirement (mistake / target came back to life). Returns True
    if the file was retired. Does NOT restore prior quarantine/retry counters --
    the next build is treated as fresh."""
    with _LockedStateFile() as data:
        if filename not in data.get("retired", {}):
            return False
        del data["retired"][filename]
        data.setdefault("notes", []).append({
            "at": _now(),
            "action": "unretire",
            "filename": filename,
            "note": note,
        })
        data["notes"] = data["notes"][-50:]
        return True


def set_breaker_state(new_state: str, reason: str) -> dict:
    """Admin-only helper. Called by reset_breaker.py. Does NOT allow setting
    'tripped' -- trips come exclusively from record_cohort evaluation."""
    if new_state not in ("closed", "half-open"):
        raise ValueError(
            f"set_breaker_state refuses {new_state!r}; allowed: closed, half-open. "
            "Trips come from record_cohort evaluation only."
        )
    with _LockedStateFile() as data:
        prev = data["state"]
        data["state"] = new_state
        data["state_changed_at"] = _now()
        data["state_changed_reason"] = f"manual ({prev} -> {new_state}): {reason}"
        data.setdefault("notes", []).append({
            "at": _now(),
            "action": "set_breaker_state",
            "from": prev,
            "to": new_state,
            "reason": reason,
        })
        data["notes"] = data["notes"][-50:]
        return dict(data)


# ── Auto-recovery + stale-quarantine revalidation ─────────────────────────────

def maybe_auto_recover(now: Optional[float] = None) -> Optional[str]:
    """Step a *stale* tripped breaker to half-open. Returns the new state if it
    transitioned, else None.

    A trip is "stale" when ALL of:
      - state == "tripped"
      - AUTO_RECOVER_AFTER_SECS > 0
      - it tripped more than AUTO_RECOVER_AFTER_SECS ago
      - NO cohort observed at/after the trip had fail_rate >= the running
        threshold (i.e. nothing has actually failed since)

    Cheap/safe on the read path: a lockless snapshot gates the common case, and
    the write lock is taken only when a transition is actually due (and state is
    re-checked under lock to avoid racing a concurrent manual reset). Going to
    half-open (never straight to closed) preserves the invariant that a real
    clean cohort is still required to fully close.
    """
    if AUTO_RECOVER_AFTER_SECS <= 0:
        return None
    snap = snapshot()
    if snap.get("state") != "tripped":
        return None
    now = time.time() if now is None else now
    changed_at = _parse_iso(snap.get("state_changed_at"))
    if changed_at is None or (now - changed_at) < AUTO_RECOVER_AFTER_SECS:
        return None
    # A failing cohort observed at/after the trip means the trip is still live.
    for c in snap.get("recent_cohorts", []):
        obs = _parse_iso(c.get("observed_at"))
        if obs is not None and obs >= changed_at and \
                c.get("fail_rate", 0) >= BREAKER_FAIL_THRESHOLD_RUNNING:
            return None
    with _LockedStateFile() as data:
        if data.get("state") != "tripped":  # raced a manual reset
            return None
        age_h = (now - changed_at) / 3600.0
        data["state"] = "half-open"
        data["state_changed_at"] = _now()
        data["state_changed_reason"] = (
            f"auto-recover: stale trip ({age_h:.1f}h old) with no failing cohort "
            f">= {BREAKER_FAIL_THRESHOLD_RUNNING:.0%} since the trip"
        )
        data.setdefault("notes", []).append({
            "at": _now(),
            "action": "auto_recover",
            "from": "tripped",
            "to": "half-open",
            "reason": data["state_changed_reason"],
        })
        data["notes"] = data["notes"][-50:]
        return "half-open"


# ---- Non-identifying keys -------------------------------------------------

#: Filenames every service unit emits. Under the pre-2026-08-03 keyspace the
#: accounting key was `Path(build['file']).name`, so ALL services shared one
#: counter for each of these -- `__init__.py` reached 19 attempts against a
#: budget of 3 and quarantined, blocking every service at once. A key that
#: cannot name one artifact cannot support a claim about one artifact.
SERVICE_UNIT_MEMBERS = frozenset({
    "service.toml", "__init__.py", "router.py", "logic.py", "contract.py",
})


def _is_nonidentifying(key: str) -> bool:
    """True if `key` is a BARE service-unit member basename.

    Bare == no directory component. `services/staged/foo/router.py` names one
    artifact and is fine; `router.py` names ~345 of them and is not. Legacy
    flat modules (`mcp_scanner.py`, `e2e_scenarios.py`, ...) are untouched --
    they are not service-unit members.
    """
    if not key or "/" in key or "\\" in key:
        return False
    return key in SERVICE_UNIT_MEMBERS


def drop_nonidentifying_keys() -> list:
    """Delete quarantine + retry entries whose key cannot identify an artifact.

    Idempotent and self-healing: it converges the state file to the post-fix
    keyspace on every read, so a rollback of the gate change simply refills
    them rather than leaving a wedge. Returns the keys dropped.

    This is a REMOVAL of a false gate, not the addition of a real one (R7).
    The entries it drops assert `missing_on_disk` about files with 300+ copies
    on disk; the assertion was never true, so nothing is being forgiven.
    """
    # Cheap lockless gate for the common (already-converged) case; the write
    # lock is taken only when there is actually something to drop.
    snap = snapshot()
    if not any(_is_nonidentifying(k)
               for b in ("quarantined", "file_retries")
               for k in snap.get(b, {})):
        return []
    dropped = []
    with _LockedStateFile() as data:
        for bucket in ("quarantined", "file_retries"):
            for key in [k for k in data.get(bucket, {}) if _is_nonidentifying(k)]:
                del data[bucket][key]
                dropped.append(f"{bucket}:{key}")
        if dropped:
            data.setdefault("notes", []).append({
                "at": _now(),
                "action": "drop_nonidentifying_keys",
                "dropped": dropped,
                "reason": ("key is a bare service-unit member basename; it "
                           "aggregates every service and identifies none"),
            })
            data["notes"] = data["notes"][-50:]
    return dropped


def _default_find_fn(root: Path, filename: str) -> bool:
    """True if <filename> exists ANYWHERE under root (any depth).

    The service unit puts service.toml / __init__.py / router.py / logic.py /
    contract.py inside services/<lane>/<name>/, so a root-only existence probe
    reports MISSING for a file that has hundreds of copies on disk. Guarded so
    an unreadable tree degrades to an honest False rather than raising inside a
    recovery sweep."""
    try:
        return any(True for _ in Path(root).rglob(filename))
    except Exception:
        return False


def release_stale_missing(exists_fn: Callable[[str], bool] = os.path.exists,
                          root: Optional[Path] = None,
                          find_fn: Optional[Callable[[Path, str], bool]] = None) -> list:
    """Release quarantine entries flagged 'missing_on_disk' whose file now EXISTS.

    Gate 8 quarantines a file as 'missing_on_disk' when it can't find the built
    artifact. If the file is later (re)built, that entry is stale -- the breaker
    never re-checks disk on its own, so false positives accumulate forever. This
    sweeps them: for each quarantined file whose reason mentions
    'missing_on_disk', release it if the file is found EITHER at <root>/<filename>
    OR anywhere beneath root (clears quarantine + retry counter). Returns the list
    of released filenames.

    The tree search is load-bearing, not defensive: service-unit members
    (service.toml, __init__.py, router.py, logic.py, contract.py) only ever exist
    at services/<lane>/<name>/<filename>, so the original root-only probe made
    their 'missing_on_disk' entries PERMANENT -- a monitored item that can never
    clear. Measured 2026-08-02: four such entries held while 327 copies of each
    were on disk.

    exists_fn/root are injectable for hermetic tests; on the host they default to
    os.path.exists against the state file's directory (the build output dir)."""
    root = root if root is not None else _resolve_state_file().parent
    find_fn = find_fn if find_fn is not None else _default_find_fn
    released = []
    snap = snapshot()
    for filename, meta in list(snap.get("quarantined", {}).items()):
        if "missing_on_disk" not in str(meta.get("reason", "")):
            continue
        # Resolve the artifact from the TREE, not from one candidate path (R1):
        # 'missing' must be measured. A root-only probe is structurally blind to
        # every service-unit member and so can never release one.
        at_root = exists_fn(str(Path(root) / filename))
        if at_root or find_fn(Path(root), filename):
            where = "at root" if at_root else "elsewhere under root"
            if release_quarantine(
                filename,
                note=f"auto: missing_on_disk flag stale; file present on disk ({where})",
            ):
                released.append(filename)
    return released


if __name__ == "__main__":
    # Self-test + CLI peek
    import sys
    s = snapshot()
    print(json.dumps(s, indent=2))
    print(f"\n[state file: {STATE_FILE}]", file=sys.stderr)