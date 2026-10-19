"""Bounded read-surface helpers for the moat freshness/registry read routes
(FU-667 / #6985 / #7016 follow-up).

WHY THIS EXISTS: the public freshness routes aggregate over the full score /
registry corpus (hundreds of thousands of rows on a small Fly Postgres). A cold
aggregate can run for 50s+, which blows past every client timeout -- the caller
sees HTTP 000 (a hang), the worker stays blocked, and the TTL cache never fills
so the NEXT request repeats the whole hang. That is the FU-667 "/freshness goes
blind" mode: not a dead backend, an UNBOUNDED query.

The fix is the same shape the /spine/health probe already uses (app/main.py
_read_surface_health): put a Postgres `statement_timeout` on the connection so a
query that exceeds the budget is CANCELLED by the database and surfaces as an
honest error -- a fast 503 the caller can act on, never a silent 45s hang that
blocks a gunicorn worker. On sqlite (dev/CI) there is no statement_timeout and
the datasets are tiny, so the guard is a no-op there.

Degrade honestly (freshness_gate.py doctrine): on timeout we raise, we never
emit a fabricated zero. A fake zero is indistinguishable from "the corpus is
empty" -- the exact silent lie these surfaces exist to prevent.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

# Budget for a single moat read-route aggregate. Generous enough that a warm
# index-backed aggregate always finishes, tight enough that a pathological seq
# scan is cancelled long before any client (or the gunicorn worker timeout)
# gives up -- so the failure mode is a fast honest 503, not a 45s hang.
READ_SURFACE_STATEMENT_TIMEOUT_MS = 8000


def arm_statement_timeout(db: Session, timeout_ms: int = READ_SURFACE_STATEMENT_TIMEOUT_MS) -> None:
    """Bound every subsequent statement on THIS session's connection.

    Postgres only: a `SET LOCAL statement_timeout` so the database itself
    cancels a runaway query. No-op on sqlite (dev/CI) where there is no such
    knob and the corpus is tiny. `SET LOCAL` is scoped to the surrounding
    transaction, which the request-scoped session opens on first use, so it
    cannot leak to a pooled connection's later checkout.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # SET LOCAL takes effect for the current transaction; a plain SELECT via the
    # ORM implicitly begins one, so arm it up front on the same connection.
    # NOTE: Postgres SET does NOT accept a bind parameter ($1) -- it demands a
    # literal, so a parameterised `SET LOCAL statement_timeout = :ms` fails with
    # `syntax error at or near "$1"`. timeout_ms is a server-controlled int
    # (coerced below), never user input, so inlining it is safe from injection.
    ms = int(timeout_ms)
    db.execute(text(f"SET LOCAL statement_timeout = {ms}"))


def is_statement_timeout(exc: BaseException) -> bool:
    """True when `exc` is a Postgres statement_timeout cancellation.

    psycopg raises QueryCanceled (SQLSTATE 57014), wrapped by SQLAlchemy as
    OperationalError. Match on the pgcode when present, else the message, so we
    distinguish a budget overrun (retriable, 503) from a real DB fault (500).
    """
    if not isinstance(exc, OperationalError):
        return False
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "57014":
        return True
    return "statement timeout" in str(exc).lower() or "canceling statement" in str(exc).lower()


def timeout_as_503(exc: BaseException) -> HTTPException:
    """Map a statement-timeout overrun to a 503 the caller can retry; re-raise
    anything else unchanged (a real DB error stays a 500)."""
    if is_statement_timeout(exc):
        return HTTPException(
            status_code=503,
            detail=("freshness aggregate exceeded the "
                    f"{READ_SURFACE_STATEMENT_TIMEOUT_MS} ms read budget; "
                    "corpus is reachable but this query is too slow right now "
                    "(retry shortly -- a warm cache serves instantly)"),
        )
    raise exc


# Background statement budget: the full corpus aggregates cost ~460s cold on
# the 1-vCPU Fly PG (distinct-servers over 4.7M scores + the never-scored
# anti-join over 560k registry rows), fundamentally more than any client will
# wait. They only move when a weekly rescore lands, so they are computed OFF
# the request path by a SINGLE elected worker and persisted to freshness_stats;
# routes read that one row. 15-min budget: headroom over the ~8-min cold
# compute, well under the DB's 15-min default statement_timeout.
BACKGROUND_STATEMENT_TIMEOUT_MS = 900_000

# Read budget for the one-row freshness_stats lookup the routes do. A primary-
# key SELECT is sub-millisecond; 5s is pure backstop against a locked table.
SNAPSHOT_READ_TIMEOUT_MS = 5000


class SharedSnapshot:
    """A DB-backed freshness snapshot: computed by ONE elected worker, persisted
    to the freshness_stats table, served O(1) by every worker and route (FU-667).

    Why DB-backed and lock-elected (not a per-worker in-memory daemon): the
    aggregates cost ~460s on the 1-vCPU moat PG. A per-worker daemon runs that
    heavy query once PER gunicorn worker -- with 2 machines x N workers, N
    concurrent 460s scans saturated the single PG (the v122 pile-up that forced
    the v122->v121 rollback). Here every worker runs a refresher loop, but each
    cycle first tries a Postgres session-level ADVISORY LOCK: only the one worker
    that gets it recomputes and writes the row; all others release instantly and
    skip. So the heavy aggregate runs at most once per cadence across the whole
    fleet, never once-per-worker.

    The route path never computes: `get()` reads one indexed row. Until the
    first compute lands the route 503s "warming" (honest -- an unknown is not a
    fabricated zero). `compute(db)` returns the JSON-serialisable payload.
    """

    def __init__(self, surface: str, compute: Callable[[Session], dict],
                 session_factory: Callable[[], Session],
                 advisory_lock_key: int,
                 ttl_seconds: int = 3600, refresh_interval_seconds: int = 1800):
        self.surface = surface
        self._compute = compute
        self._session_factory = session_factory
        self.advisory_lock_key = advisory_lock_key
        self.ttl_seconds = ttl_seconds
        self.refresh_interval_seconds = refresh_interval_seconds
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None

    # --- persistence helpers (import here to avoid a cycle at module load) ---
    def _read_row(self, db: Session):
        from app.models import FreshnessStat
        return db.get(FreshnessStat, self.surface)

    def _write_row(self, db: Session, payload: dict, compute_seconds: float) -> None:
        from datetime import datetime, timezone
        from app.models import FreshnessStat
        row = db.get(FreshnessStat, self.surface)
        now = datetime.now(timezone.utc)
        if row is None:
            db.add(FreshnessStat(surface=self.surface, payload=payload,
                                 computed_at=now, compute_seconds=compute_seconds))
        else:
            row.payload = payload
            row.computed_at = now
            row.compute_seconds = compute_seconds
        db.commit()

    def _row_age_seconds(self, db: Session) -> Optional[float]:
        from datetime import datetime, timezone
        row = self._read_row(db)
        if row is None:
            return None
        ca = row.computed_at
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ca).total_seconds()

    def refresh_if_stale(self, force: bool = False) -> bool:
        """If this surface's row is missing or older than refresh_interval, try
        to grab the advisory lock and recompute+persist it. Returns True if THIS
        call did the compute. Never raises (records _last_error). Safe to call
        from any worker: the lock serialises the heavy work to one at a time."""
        db = self._session_factory()
        try:
            if not force:
                age = self._row_age_seconds(db)
                if age is not None and age < self.refresh_interval_seconds:
                    return False  # fresh enough; some worker already did it
            # Elect: only the lock holder computes. pg_try_advisory_lock is
            # non-blocking -- a worker that loses the election returns at once.
            is_pg = db.get_bind().dialect.name == "postgresql"
            got_lock = True
            if is_pg:
                got_lock = bool(db.execute(
                    text("SELECT pg_try_advisory_lock(:k)")
                    .bindparams(k=self.advisory_lock_key)).scalar())
            if not got_lock:
                return False
            try:
                compute_db = self._session_factory()
                try:
                    # Re-check under the lock: another worker may have just
                    # written it while we waited to acquire.
                    if not force:
                        age = self._row_age_seconds(compute_db)
                        if age is not None and age < self.refresh_interval_seconds:
                            return False
                    arm_statement_timeout(compute_db, BACKGROUND_STATEMENT_TIMEOUT_MS)
                    t0 = time.monotonic()
                    payload = self._compute(compute_db)
                    self._write_row(compute_db, payload, round(time.monotonic() - t0, 1))
                    with self._lock:
                        self._last_error = None
                    return True
                finally:
                    compute_db.close()
            finally:
                if is_pg:
                    db.execute(text("SELECT pg_advisory_unlock(:k)")
                               .bindparams(k=self.advisory_lock_key))
                    db.commit()
        except Exception as e:  # noqa: BLE001 -- a refresher must never die
            with self._lock:
                self._last_error = f"{type(e).__name__}: {e}"
            return False
        finally:
            db.close()

    # After a FAILED/elected-skip cycle, retry soon rather than waiting the full
    # cadence -- otherwise a transient error (a missing GRANT, a lock lost to a
    # peer, a one-off timeout) blanks the surface for the whole interval. A
    # successful write sleeps the full cadence (the data only moves weekly).
    _ERROR_RETRY_SECONDS = 60

    def _run_loop(self) -> None:
        while True:
            did = self.refresh_if_stale()
            with self._lock:
                errored = self._last_error is not None
            # did == True: wrote a row -> sleep the cadence.
            # did == False + no error: another worker owns a fresh row -> cadence.
            # did == False + error: this surface may be blank -> retry soon.
            if (not did) and errored:
                time.sleep(self._ERROR_RETRY_SECONDS)
            else:
                time.sleep(self.refresh_interval_seconds)

    def start(self) -> None:
        """Start the refresher daemon. Idempotent, never fatal. Every worker
        runs one, but the advisory lock means only one computes at a time."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run_loop, name=f"snapshot-{self.surface}", daemon=True)
            self._thread.start()

    def get(self, db: Session) -> dict:
        """Read the persisted row O(1) and return its payload with staleness
        metadata. 503 while the first compute is still pending. Lazy-kicks the
        refresher so a never-warmed surface starts computing on first hit."""
        if self._thread is None or not self._thread.is_alive():
            self.start()
        arm_statement_timeout(db, SNAPSHOT_READ_TIMEOUT_MS)
        row = self._read_row(db)
        if row is None:
            with self._lock:
                err = self._last_error
            detail = ("freshness snapshot is warming up (first compute in "
                      "flight); retry shortly")
            if err:
                detail = f"freshness snapshot unavailable: {err}"
            raise HTTPException(status_code=503, detail=detail)
        from datetime import datetime, timezone
        ca = row.computed_at
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ca).total_seconds()
        out = dict(row.payload)
        out["cache_age_seconds"] = round(age, 1)
        out["cache_ttl_seconds"] = self.ttl_seconds
        out["stale"] = age > self.ttl_seconds
        out["computed_at"] = ca.isoformat()
        return out


if __name__ == "__main__":
    # Self-test: sqlite no-op + timeout classification (no Postgres needed).
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    TS = sessionmaker(bind=eng, future=True)
    s = TS()
    # 1. arm is a silent no-op on sqlite and does not break a subsequent query.
    arm_statement_timeout(s)
    assert s.execute(text("SELECT 1")).scalar() == 1
    s.close()

    # 1b. The SET statement MUST inline the timeout as a literal -- Postgres SET
    # rejects a bind parameter ($1) with a syntax error (regression guard: this
    # 500'd prod on first deploy because the ms was bound, not inlined). Capture
    # the SQL the arm would execute on a postgres-dialect session and assert it
    # carries a literal integer, no ':ms' / '$' placeholder.
    from sqlalchemy.dialects import postgresql as _pg

    class _CaptureSession:
        dialect = _pg.dialect()
        executed: list[str] = []

        def get_bind(self):
            return self

        def execute(self, clause):
            self.executed.append(str(clause))

    cap = _CaptureSession()
    arm_statement_timeout(cap, 8000)
    assert cap.executed, "arm must issue a SET on a postgresql session"
    sql = cap.executed[0]
    assert "8000" in sql, f"timeout must be an inlined literal: {sql!r}"
    assert ":ms" not in sql and "$" not in sql, f"no bind placeholder allowed in SET: {sql!r}"

    # 2. a plain OperationalError that is NOT a timeout is re-raised untouched.
    non_timeout = OperationalError("stmt", {}, Exception("connection reset"))
    assert is_statement_timeout(non_timeout) is False
    reraised = False
    try:
        timeout_as_503(non_timeout)
    except OperationalError:
        reraised = True
    assert reraised, "non-timeout OperationalError must re-raise, not become 503"

    # 3. a timeout (by SQLSTATE and by message) maps to a 503.
    class _Orig(Exception):
        sqlstate = "57014"

    to = OperationalError("stmt", {}, _Orig("canceling statement due to statement timeout"))
    assert is_statement_timeout(to) is True
    http = timeout_as_503(to)
    assert http.status_code == 503, http.status_code

    by_msg = OperationalError("stmt", {}, Exception("canceling statement due to statement timeout"))
    assert is_statement_timeout(by_msg) is True

    # 4. a non-OperationalError is never a timeout.
    assert is_statement_timeout(ValueError("nope")) is False

    # 5. SharedSnapshot (DB-backed, sqlite): 503 while unwarmed, serves the
    #    persisted row after a refresh, computes at most once when fresh, and a
    #    raising compute leaves no row + surfaces the error.
    import os as _os
    _os.environ.setdefault("APP_JWT_SECRET", "test-secret-at-least-32-bytes-long-xx")
    _os.environ.setdefault("APP_ENV", "dev")
    _os.environ.setdefault("DATABASE_URL", "sqlite://")
    from app.db import Base as _AppBase
    from app import models as _models  # noqa: F401 -- registers FreshnessStat

    eng2 = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    _AppBase.metadata.create_all(eng2)
    TS2 = sessionmaker(bind=eng2, future=True)

    calls = {"n": 0}

    def _compute_ok(db):
        calls["n"] += 1
        return {"scores_rows": 42}

    snap = SharedSnapshot("t_ok", _compute_ok, TS2, advisory_lock_key=991001,
                          ttl_seconds=3600, refresh_interval_seconds=1800)
    # Before any compute there is no row -> reading it must 503 "warming".
    # NOTE: do NOT call snap.get() here -- get() lazy-starts the daemon, which
    # would race the explicit refresh below. Assert the unwarmed state directly.
    assert snap._read_row(TS2()) is None, "no row before the first compute"
    # Explicit refresh (sqlite: no advisory lock, so it always computes + writes).
    did = snap.refresh_if_stale()
    assert did is True, "first refresh must compute"
    assert calls["n"] == 1, calls
    got = snap.get(TS2())
    assert got["scores_rows"] == 42, got
    assert got["stale"] is False and "computed_at" in got, got
    # A second refresh within the interval must NOT recompute (row is fresh).
    did2 = snap.refresh_if_stale()
    assert did2 is False and calls["n"] == 1, (did2, calls)
    # force=True recomputes regardless of freshness.
    assert snap.refresh_if_stale(force=True) is True and calls["n"] == 2, calls

    # A raising compute leaves no row (surface never warmed) -> 503 + error.
    def _compute_boom(db):
        raise RuntimeError("db blip")

    snap2 = SharedSnapshot("t_boom", _compute_boom, TS2, advisory_lock_key=991002)
    snap2.refresh_if_stale()
    boomed = False
    try:
        snap2.get(TS2())
    except HTTPException as he:
        boomed = he.status_code == 503 and "db blip" in he.detail
    assert boomed, "a never-succeeded snapshot must 503 and surface the error"

    print("PASS")
