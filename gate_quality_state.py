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
import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATE_FILE = Path("/home/workspace/zo_sentinel/gate_quality_state.json")

# Tunables (also documented in gate_8_new_module.py for operator reference)
BREAKER_FAIL_THRESHOLD_SINGLE    = 0.40   # 40% in one cohort trips
BREAKER_FAIL_THRESHOLD_RUNNING   = 0.30   # 30% * 3 consecutive also trips
BREAKER_RUNNING_WINDOW           = 3      # consecutive cohorts to watch
MIN_COHORT_SIZE                  = 4      # cohorts smaller than this don't count
MAX_REBUILDS                     = 3      # per-file retry budget

_DEFAULT_STATE = {
    "state": "closed",                 # closed | tripped | half-open
    "state_changed_at": None,          # ISO timestamp of last transition
    "state_changed_reason": None,      # human-readable note
    "recent_cohorts": [],              # list of {id, size, fail_rate, observed_at}
    "file_retries": {},                # {filename: {attempts: N, last_failed_at: iso, last_error: str}}
    "quarantined": {},                 # {filename: {quarantined_at: iso, reason: str, attempts_when_quarantined: N}}
    "notes": [],                       # human-appendable operator notes
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _LockedStateFile:
    """Context manager for read-modify-write with fcntl advisory lock.
    Creates the file with defaults if missing."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.fh = None
        self.data = None

    def __enter__(self):
        # Ensure parent exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open for read+write; create if missing
        if not self.path.exists():
            self.path.write_text(json.dumps(_DEFAULT_STATE, indent=2))
        self.fh = open(self.path, "r+")
        # Exclusive lock, block until acquired (bounded by 5s via timeout loop)
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
    return snapshot().get("state", "closed")


def is_quarantined(filename: str) -> bool:
    return filename in snapshot().get("quarantined", {})


def retry_count(filename: str) -> int:
    q = snapshot().get("file_retries", {})
    return q.get(filename, {}).get("attempts", 0)


def may_rebuild(filename: str) -> tuple[bool, str]:
    """Primary query used by directive_knowledge_sources. Returns
    (ok, reason). Reason is always populated so the generator prompt can
    include it."""
    snap = snapshot()
    if snap["state"] == "tripped":
        return False, "circuit breaker tripped -- manual reset required"
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


if __name__ == "__main__":
    # Self-test + CLI peek
    import sys
    s = snapshot()
    print(json.dumps(s, indent=2))
    print(f"\n[state file: {STATE_FILE}]", file=sys.stderr)