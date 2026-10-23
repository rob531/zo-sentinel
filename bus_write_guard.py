"""Resolve the write target from the LIVE bus before posting, and spool rather than lose.

WHY THIS EXISTS -- measured 2026-10-05 (improvement-loop cycle-0181), on
/home/workspace/logs/write_service.log, 17790 lines covering 2026-10-04T07:00 ->
2026-10-05T06:37 (23.6h):

    mcp_discovery_candidates  4172 rows  Catalog Error: table does not exist
    mcp_threat_associations   4089 rows  Binder Error: no UNIQUE/PK for ON CONFLICT
    mesh_events                 23 rows  Constraint Error: NOT NULL agent_id
    ------------------------------------------------------------------------
                              8284 rows/day acknowledged with 2xx, stored nowhere

`write_service`'s POST /write answers {"ok": true, "queued": 1} -- an ENQUEUE receipt.
It is returned before the row reaches the store, so a 2xx says the bus accepted the
request, never that the row exists.  Every caller on this fleet counted the 2xx and
published it as a write.  cycle-0179 cured exactly this in attestation_engine.py by
re-deriving the count from the store; that was one door of N, and this is the shared one.

HARNESS_DOCTRINE this implements:
  R1  the target is resolved from the live bus, never from a repo schema file
  R6  UNKNOWN is not ABSENT and ABSENT is not zero -- three states, never two.  The bug
      this replaces read `information_schema.columns` and treated an EMPTY result as a
      successful verification (SCHEMA_VERIFIED = True over a table that does not exist)
  R7  recovery over restriction -- a row whose target cannot be resolved is spooled to
      disk and replayable, not dropped and not used to halt the daemon

Nothing here adds a gate, a required check or an approval.  It changes what a caller is
allowed to call "written".
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

PRESENT = "PRESENT"
ABSENT = "ABSENT"
UNKNOWN = "UNKNOWN"

DEFAULT_SPOOL_DIR = os.environ.get("BUS_WRITE_SPOOL_DIR", "/home/workspace/logs/bus_spool")
RESOLVE_TTL_SECONDS = 300

_CACHE: Dict[str, Tuple[str, str, float]] = {}
_CACHE_LOCK = threading.Lock()


def reset_cache() -> None:
    """Drop the presence cache.  Tests and a replay after a schema change call this."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _default_logger(msg: str) -> None:
    print(msg, flush=True)


def resolve_table(
    table: str,
    query_url: str,
    *,
    timeout: float = 10.0,
    querier: Optional[Callable[[str, str, float], Any]] = None,
    use_cache: bool = True,
) -> Tuple[str, str]:
    """Return (state, basis) where state is PRESENT | ABSENT | UNKNOWN.

    basis is a one-line string naming HOW the state was resolved (R5), suitable for a
    log line or an issue comment.  It is never empty.

    The query asks information_schema.tables for the table's OWN row, so an empty result
    means the table is absent.  Reading information_schema.columns cannot distinguish
    "absent table" from "answered with no columns", which is the defect this replaces.
    A transport failure, a non-200, or a malformed body is UNKNOWN -- never ABSENT.
    """
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(table)
        if hit and (time.time() - hit[2]) < RESOLVE_TTL_SECONDS:
            return hit[0], hit[1] + " (cached)"

    sql = (
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_name = '{table}'"
    )
    q = querier or _post_query
    try:
        body = q(query_url, sql, timeout)
    except Exception as exc:  # transport, DNS, timeout, connection reset
        return UNKNOWN, f"information_schema.tables unreachable at {query_url}: {exc!r}"

    if body is None:
        return UNKNOWN, f"information_schema.tables returned no body from {query_url}"
    if not isinstance(body, dict):
        return UNKNOWN, f"information_schema.tables returned {type(body).__name__}, not an object"
    if "rows" not in body:
        return UNKNOWN, f"information_schema.tables answer has no 'rows' key: {sorted(body)[:6]}"

    rows = body.get("rows") or []
    names = {r.get("table_name") for r in rows if isinstance(r, dict)}
    if table in names:
        state, basis = PRESENT, f"information_schema.tables @ {query_url} returned the row"
    else:
        state, basis = ABSENT, (
            f"information_schema.tables @ {query_url} answered 200 with "
            f"{len(rows)} row(s) and none named {table}"
        )

    if use_cache:
        with _CACHE_LOCK:
            _CACHE[table] = (state, basis, time.time())
    return state, basis


def _post_query(query_url: str, sql: str, timeout: float) -> Any:
    import requests  # imported lazily so the module imports in a bare test env

    resp = requests.post(query_url, json={"sql": sql}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {query_url}")
    return resp.json()


def _post_write(write_url: str, table: str, row: Dict[str, Any], timeout: float) -> bool:
    import requests

    resp = requests.post(
        write_url, json={"table": table, "rows": row, "wait": True}, timeout=timeout
    )
    return resp.status_code in (200, 201)


def _row_key(row: Dict[str, Any]) -> str:
    """Stable identity for a spooled row, so a replay is idempotent."""
    canon = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


@dataclass
class GuardedWriteResult:
    """What actually happened.  `posted` is the only number a caller may call written,
    and even that is an enqueue count -- see `stored` for a store-derived number."""

    table: str
    state: str
    posted: int = 0
    spooled: int = 0
    failed: int = 0
    basis: str = ""
    spool_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == PRESENT and self.failed == 0

    def summary(self) -> str:
        return (
            f"{self.table}: state={self.state} posted={self.posted} "
            f"spooled={self.spooled} failed={self.failed} (basis: {self.basis})"
        )


def _spool_file(spool_dir: str, table: str) -> Path:
    p = Path(spool_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{table}.jsonl"


def spool_rows(table: str, rows: Sequence[Dict[str, Any]], spool_dir: str, reason: str) -> Tuple[int, str]:
    """Append rows to the table's spool.  Returns (count_written, path).

    Idempotent against re-delivery of the same row: a key already present in the spool
    is not appended again, so a daemon that retries the same page does not grow the file
    without bound.
    """
    path = _spool_file(spool_dir, table)
    existing = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.add(json.loads(line).get("_key"))
                except Exception:
                    continue
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            key = _row_key(row)
            if key in existing:
                continue
            existing.add(key)
            fh.write(
                json.dumps(
                    {"_key": key, "_spooled_at": time.time(), "_reason": reason, "row": row},
                    default=str,
                )
                + "\n"
            )
            n += 1
    return n, str(path)


def guarded_write(
    table: str,
    rows: Iterable[Dict[str, Any]],
    *,
    write_url: str,
    query_url: str,
    spool_dir: str = DEFAULT_SPOOL_DIR,
    timeout: float = 10.0,
    querier: Optional[Callable[[str, str, float], Any]] = None,
    poster: Optional[Callable[[str, str, Dict[str, Any], float], bool]] = None,
    logger: Optional[Callable[[str], None]] = None,
    use_cache: bool = True,
) -> GuardedWriteResult:
    """Post rows only to a target proven PRESENT on the live bus; spool them otherwise.

    ABSENT  -- nothing is posted.  Posting would earn a 2xx and destroy the row.
    UNKNOWN -- nothing is posted either, because an unresolvable target is not a
               resolved-present one (R6).  The rows are on disk and replayable, so the
               cost of being wrong in this direction is a replay, not a loss.
    """
    log = logger or _default_logger
    batch = [r for r in rows]
    result = GuardedWriteResult(table=table, state=UNKNOWN)
    if not batch:
        state, basis = resolve_table(table, query_url, timeout=timeout, querier=querier, use_cache=use_cache)
        result.state, result.basis = state, basis
        return result

    state, basis = resolve_table(table, query_url, timeout=timeout, querier=querier, use_cache=use_cache)
    result.state, result.basis = state, basis

    if state != PRESENT:
        n, path = spool_rows(table, batch, spool_dir, reason=state)
        result.spooled, result.spool_path = n, path
        log(
            f"ERROR: {table} is {state} on the bus -- posted 0 of {len(batch)} row(s), "
            f"spooled {n} to {path}. A 2xx here would have been an enqueue receipt for a "
            f"row that is never stored. basis: {basis}"
        )
        return result

    post = poster or _post_write
    for row in batch:
        try:
            if post(write_url, table, row, timeout):
                result.posted += 1
            else:
                result.failed += 1
        except Exception as exc:
            result.failed += 1
            if len(result.errors) < 5:
                result.errors.append(repr(exc))
    if result.failed:
        n, path = spool_rows(
            table,
            batch[result.posted:],
            spool_dir,
            reason="POST_FAILED",
        )
        result.spooled, result.spool_path = n, path
    return result


def replay_spool(
    table: str,
    *,
    write_url: str,
    query_url: str,
    spool_dir: str = DEFAULT_SPOOL_DIR,
    timeout: float = 10.0,
    limit: int = 5000,
    querier: Optional[Callable[[str, str, float], Any]] = None,
    poster: Optional[Callable[[str, str, Dict[str, Any], float], bool]] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> GuardedWriteResult:
    """Drain a table's spool once the target exists.  Safe to run on a schedule.

    Idempotent: rows that post successfully are removed from the spool, so a second
    run with nothing new posts 0.  If the target is still not PRESENT, nothing is
    posted and nothing is removed.
    """
    log = logger or _default_logger
    reset_cache()
    state, basis = resolve_table(table, query_url, timeout=timeout, querier=querier, use_cache=False)
    result = GuardedWriteResult(table=table, state=state, basis=basis)
    path = _spool_file(spool_dir, table)
    result.spool_path = str(path)
    if not path.exists():
        return result
    if state != PRESENT:
        log(f"replay {table}: target is {state}, left the spool alone. basis: {basis}")
        return result

    pending: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                pending.append(json.loads(line))
            except Exception:
                continue

    post = poster or _post_write
    remaining: List[Dict[str, Any]] = []
    for i, entry in enumerate(pending):
        if i >= limit:
            remaining.append(entry)
            continue
        row = entry.get("row")
        if not isinstance(row, dict):
            continue
        try:
            if post(write_url, table, row, timeout):
                result.posted += 1
            else:
                result.failed += 1
                remaining.append(entry)
        except Exception as exc:
            result.failed += 1
            remaining.append(entry)
            if len(result.errors) < 5:
                result.errors.append(repr(exc))

    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for entry in remaining:
            fh.write(json.dumps(entry, default=str) + "\n")
    os.replace(tmp, path)
    result.spooled = len(remaining)
    log(
        f"replay {table}: posted {result.posted}, failed {result.failed}, "
        f"{len(remaining)} left in {path}"
    )
    return result


def spool_depth(table: str, spool_dir: str = DEFAULT_SPOOL_DIR) -> int:
    """How many rows are waiting.  A number a lane can watch without a new gate."""
    path = _spool_file(spool_dir, table)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())
