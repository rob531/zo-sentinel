#!/usr/bin/env python3
"""
zo_sentinel.bus -- THE read door to the write-service bus (:8772/query).

WHY THIS MODULE EXISTS
----------------------
The bus silently caps a result set. Measured on the LIVE runtime 2026-09-28 via
``:8772/query``:

    response keys                           ['count', 'rows']  (no truncation flag)
    unpaginated information_schema.columns   200 rows
    count(*) over the same predicate         373
    information_schema.tables                46

So an unpaginated read of that relation returns 54% of it and says nothing. The
response's ``count`` field equals ``len(rows)`` on every query -- 7 for a
deliberate 7-row read, 200 for the capped read -- so it cannot distinguish
*capped at 200* from *there were exactly 200*. That is the gap #3997 exists to
close, and it is why the terminating condition below is "a page came back SHORT"
and not "the bus said it truncated".

Paging does NOT need that flag. Ask for pages, stop when one comes back short,
then PROVE the assembled row count equals the ``count(*)`` the bus reports.

THE REASON THIS IS A MODULE AND NOT A PARAGRAPH
-----------------------------------------------
Before this file, pagination had been solved privately FIVE times in this tree
(``tools/bus_catalog_snapshot._paginated``, ``refresh_schema_doc``,
``zo_sentinel/probes/duckdb_schema_uptime_probe``,
``signal_corpus_export.ws_query_paginated_with_target``,
``signal_labeler_student.ws_query_paginated``) while ``def ws_query`` -- the
un-paginated single-request read -- was copy-pasted into 1909 files of the live
runtime (measured 2026-09-28: ``grep -rl 'def ws_query' --include=*.py``).
``schema_kl.py`` already records the cause in a comment: *"ws_query is not one
helper -- it is copy-pasted per module"*. A hazard that is described but has no
constructor gets re-earned; #4003's "583 unbounded row reads across 313 files"
is that arithmetic, not 583 independent defects.

RECONCILIATION IS THE PART THAT MAKES PAGING TRUSTWORTHY
--------------------------------------------------------
Stopping on a short page is sound only while the page size is <= the server's
cap. If the cap is ever LOWER than the page size, EVERY page comes back short,
the loop stops after one request, and the caller believes a partial answer is
complete -- the original bug wearing the fix's clothes. Neither reader repaired
in #4662 can detect that: both page at exactly 200, the observed cap, with no
reconciliation. ``query_all`` reconciles by default and raises rather than
return a short answer. Unknown is not zero.

PUBLIC SURFACE
--------------
::

    from zo_sentinel import bus

    rows = bus.query("SELECT 1 AS ok")          # a read BOUNDED by construction
    rows = bus.query_all(SQL_WITH_ORDER_BY)     # every row, proven complete
    n    = bus.count_of(SQL_WITH_ORDER_BY)      # count(*) over that statement

Dependencies: stdlib + ``requests``. No duckdb, no pydantic -- this is imported
by probes and CI smoke tests on minimal environments.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import requests

__all__ = [
    "BusError",
    "BusTruncated",
    "BusPaginationError",
    "OBSERVED_ROW_CAP",
    "DEFAULT_PAGE_ROWS",
    "count_of",
    "query",
    "query_all",
    "query_url",
]

# A property of the server, re-measured 2026-09-28. Recorded here so a caller
# choosing a page size can be REFUSED for choosing one that cannot detect
# truncation -- see query_all().
OBSERVED_ROW_CAP = 200

DEFAULT_PAGE_ROWS = 150          # deliberately BELOW the observed cap
DEFAULT_MAX_PAGES = 2000         # 300k rows; a runaway loop is a bug
DEFAULT_TIMEOUT = 30

_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_OFFSET_RE = re.compile(r"\bOFFSET\b", re.IGNORECASE)
_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)

# Keys a future write_service might use for the #3997 flag. Absence means
# UNKNOWN, never False.
_TRUNCATION_KEYS = ("truncated", "is_truncated", "row_limit_hit")


class BusError(RuntimeError):
    """The bus could not be read. Never degraded into an empty result."""


class BusTruncated(BusError):
    """The bus DECLARED it truncated (post-#3997) and the caller did not page."""


class BusPaginationError(BusError):
    """Paging assembled a row count the bus does not agree with."""


def query_url(url: Optional[str] = None) -> str:
    """Resolve the /query endpoint. Env first, so the RUNTIME decides -- not a
    literal baked into 1909 copies."""
    if url:
        return url
    base = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772").rstrip("/")
    return base + "/query"


def _post(
    sql: str,
    params: Optional[list] = None,
    url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"sql": sql}
    if params is not None:
        payload["params"] = params
    try:
        resp = requests.post(query_url(url), json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:                   # noqa: BLE001 -- re-raised loudly
        raise BusError(
            "%s reading the bus: %s" % (type(exc).__name__, str(exc)[:200])
        ) from None
    return body if isinstance(body, dict) else {}


def _declared_truncation(body: Dict[str, Any]) -> Optional[bool]:
    """True / False when the bus says so, None when it does not say.

    ``count`` is NOT such a flag: it equals len(rows) on every query. Reading it
    as one is the mistake #3997 was filed about.
    """
    for key in _TRUNCATION_KEYS:
        if key in body:
            return bool(body[key])
    return None


def query(
    sql: str,
    params: Optional[list] = None,
    url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """One request. For reads that are BOUNDED by construction.

    If the bus ever ships #3997's flag and sets it, this raises instead of
    handing back a silently short list -- which is what turns that patch into an
    improvement rather than a precondition for 583 callers.
    """
    body = _post(sql, params=params, url=url, timeout=timeout)
    if _declared_truncation(body) is True:
        raise BusTruncated(
            "the bus declared this result truncated; use query_all(): " + sql[:160]
        )
    rows = body.get("rows")
    return list(rows) if rows else []


def _count_from_rows(rows: List[Dict[str, Any]], stmt: str) -> int:
    if not rows:
        raise BusError("count query returned no rows: " + stmt[:160])
    first = rows[0]
    for key in ("n", "count", "cnt", "c"):
        if key in first:
            return int(first[key])
    vals = list(first.values())
    if len(vals) == 1:
        return int(vals[0])
    raise BusError(
        "count query returned columns %r -- name the count column 'n' so this is "
        "unambiguous" % (sorted(first.keys())[:6],)
    )


def count_of(
    sql: str,
    params: Optional[list] = None,
    url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    """``count(*)`` over ``sql`` as a subquery.

    Verified against the live bus 2026-09-28: wrapping the information_schema
    statement this way returns 373, the same as the direct count -- so the
    reconciliation in query_all() needs nothing from the caller.
    """
    wrapped = "SELECT count(*) AS n FROM (%s) AS _zo_paged_sub" % sql
    return _count_from_rows(
        query(wrapped, params=params, url=url, timeout=timeout), wrapped
    )


def query_all(
    sql: str,
    params: Optional[list] = None,
    url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    page_rows: int = DEFAULT_PAGE_ROWS,
    max_pages: int = DEFAULT_MAX_PAGES,
    reconcile: bool = True,
    count_sql: Optional[str] = None,
    unordered_ok: bool = False,
    what: str = "rows",
) -> List[Dict[str, Any]]:
    """Read EVERY row of ``sql``, and prove none were dropped.

    ``sql`` must carry its own ORDER BY and must NOT carry LIMIT/OFFSET -- this
    function appends those. An unordered LIMIT/OFFSET walk can silently drop and
    duplicate rows, so it is refused rather than accepted with a comment.

    Raises rather than return a short answer:
      * ``ValueError``          -- the statement or the page size is unsafe
      * ``BusPaginationError``  -- assembled count disagrees with count(*)
      * ``RuntimeError``        -- OFFSET appears to be ignored (runaway guard)
    """
    if _LIMIT_RE.search(sql) or _OFFSET_RE.search(sql):
        raise ValueError(
            "query_all() appends LIMIT/OFFSET; the statement must not carry them: "
            + sql[:160]
        )
    if not unordered_ok and not _ORDER_BY_RE.search(sql):
        raise ValueError(
            "query_all() needs a total order -- LIMIT/OFFSET without ORDER BY can "
            "drop and duplicate rows. Add ORDER BY, or pass unordered_ok=True if "
            "the relation is genuinely unordered and you accept that: " + sql[:160]
        )
    if page_rows < 1:
        raise ValueError("page_rows must be >= 1")
    if page_rows >= OBSERVED_ROW_CAP and not reconcile:
        raise ValueError(
            "page_rows=%d is >= the observed server cap (%d) with reconcile=False: "
            "every page would come back short, the loop would stop after one "
            "request, and a partial answer would look complete. Lower page_rows, "
            "or leave reconcile on." % (page_rows, OBSERVED_ROW_CAP)
        )

    expected: Optional[int] = None
    if reconcile:
        if count_sql is None:
            expected = count_of(sql, params=params, url=url, timeout=timeout)
        else:
            expected = _count_from_rows(
                query(count_sql, params=params, url=url, timeout=timeout), count_sql
            )

    rows: List[Dict[str, Any]] = []
    for page in range(max_pages):
        offset = page * page_rows
        got = query(
            "%s LIMIT %d OFFSET %d" % (sql, page_rows, offset),
            params=params,
            url=url,
            timeout=timeout,
        )
        rows.extend(got)
        if len(got) < page_rows:
            break
    else:
        raise RuntimeError(
            "%s paging exceeded %d pages (%d rows) -- refusing to loop; the bus is "
            "likely ignoring OFFSET" % (what, max_pages, len(rows))
        )

    if expected is not None and len(rows) != expected:
        raise BusPaginationError(
            "pagination mismatch on %s: assembled %d, bus reports %d. Refusing to "
            "hand back a partial read." % (what, len(rows), expected)
        )
    return rows
