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


# Background statement budget: the full freshness compute costs ~460s cold on
# the small Fly PG (measured end-to-end against prod: distinct-servers GROUP BY
# over 4.68M score rows + the never-scored anti-join over 560k registry rows,
# cold seq scans), fundamentally more than any client will wait. It only moves
# when a weekly rescore lands, so it is computed OFF the request path by a
# daemon on a 15-min budget (headroom over the ~8-min cold compute, still well
# under the DB's 15-min default statement_timeout), and the route serves the
# cached snapshot. No client ever waits this long -- the route reads the store.
BACKGROUND_STATEMENT_TIMEOUT_MS = 900_000


class BackgroundSnapshot:
    """A TTL snapshot computed by a background thread, served instantly by the
    route (FU-667).

    The outage this closes: /freshness / /registry/freshness aggregate over the
    whole corpus; a cold compute is ~50s, longer than any client waits, so a
    request-time compute either hangs (old) or 503s forever (the cache can never
    fill from the request path). This decouples the two: a daemon recomputes on
    a generous budget and stores the payload; the route reads the stored payload
    with no DB work. Until the first compute lands the route 503s "warming"
    (honest -- an unknown is not a fabricated zero).

    `compute(db)` must accept a Session and return the payload. `session_factory`
    is app.db.SessionLocal. Thread-safe; at most one refresh runs at a time.
    """

    def __init__(self, name: str, compute: Callable[[Session], dict],
                 session_factory: Callable[[], Session],
                 ttl_seconds: int = 600, refresh_interval_seconds: int = 300):
        self.name = name
        self._compute = compute
        self._session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self.refresh_interval_seconds = refresh_interval_seconds
        self._lock = threading.Lock()
        self._payload: Optional[dict] = None
        self._computed_at: float = 0.0
        self._refreshing = False
        self._last_error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None

    def _refresh_once(self) -> None:
        db = self._session_factory()
        try:
            arm_statement_timeout(db, BACKGROUND_STATEMENT_TIMEOUT_MS)
            payload = self._compute(db)
            with self._lock:
                self._payload = payload
                self._computed_at = time.monotonic()
                self._last_error = None
        except Exception as e:  # noqa: BLE001 -- a background daemon must never die
            with self._lock:
                self._last_error = f"{type(e).__name__}: {e}"
        finally:
            db.close()

    def _run_loop(self) -> None:
        while True:
            self._refresh_once()
            with self._lock:
                self._refreshing = False
            time.sleep(self.refresh_interval_seconds)

    def start(self) -> None:
        """Start the daemon refresher. Idempotent, never fatal."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._refreshing = True
            self._thread = threading.Thread(
                target=self._run_loop, name=f"snapshot-{self.name}", daemon=True)
            self._thread.start()

    def _kick(self) -> None:
        """Ensure the refresher is running (lazy start for workers that never
        ran lifespan, e.g. a fresh gunicorn fork)."""
        if self._thread is None or not self._thread.is_alive():
            self.start()

    def get(self) -> dict:
        """Return the cached payload with cache-age metadata, or 503 while the
        first compute is still in flight / has only ever errored."""
        self._kick()
        with self._lock:
            payload = self._payload
            computed_at = self._computed_at
            last_error = self._last_error
        if payload is None:
            detail = ("freshness snapshot is warming up (first compute in "
                      "flight); retry shortly")
            if last_error:
                detail = f"freshness snapshot unavailable: {last_error}"
            raise HTTPException(status_code=503, detail=detail)
        out = dict(payload)
        age = round(time.monotonic() - computed_at, 1)
        out["cache_age_seconds"] = age
        out["cache_ttl_seconds"] = self.ttl_seconds
        out["stale"] = age > self.ttl_seconds
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

    # 5. BackgroundSnapshot: 503 while warming, serves payload once computed,
    #    survives a compute that raises.
    class _FakeSession:
        dialect = type("D", (), {"name": "sqlite"})()

        def get_bind(self):
            return self

        def execute(self, *a, **k):
            return None

        def close(self):
            pass

    calls = {"n": 0}

    def _compute_ok(db):
        calls["n"] += 1
        return {"scores_rows": 42}

    snap = BackgroundSnapshot("test", _compute_ok, _FakeSession,
                              ttl_seconds=600, refresh_interval_seconds=3600)
    # Before any compute: 503 warming.
    raised_503 = False
    try:
        snap.get()
    except HTTPException as he:
        raised_503 = he.status_code == 503
    assert raised_503, "must 503 before the first compute lands"
    # Drive one synchronous refresh (no daemon needed for the test).
    snap._refresh_once()
    got = snap.get()
    assert got["scores_rows"] == 42, got
    assert "cache_age_seconds" in got and got["stale"] is False, got

    # A compute that raises leaves the LAST good payload in place, records error.
    def _compute_boom(db):
        raise RuntimeError("db blip")

    snap2 = BackgroundSnapshot("boom", _compute_boom, _FakeSession)
    snap2._refresh_once()
    boomed = False
    try:
        snap2.get()
    except HTTPException as he:
        boomed = he.status_code == 503 and "db blip" in he.detail
    assert boomed, "a never-succeeded snapshot must 503 and surface the error"

    print("PASS")
