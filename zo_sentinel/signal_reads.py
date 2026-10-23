#!/usr/bin/env python3
"""zo_sentinel.signal_reads -- the ONE read of a server's CURRENT signal scores.

WHY THIS MODULE EXISTS
----------------------
``mcp_signal_scores`` is a rescore HISTORY, not current state. Measured on the
LIVE bus 2026-10-01 (``:8772/query``) for ``server_id='@goke/mcp'``::

    rows for that one server                 12,834
    distinct signal_name                         12
    distinct scored_at                       12,716
    rows for signal_name='composite' alone    2,136

Four LIVE services (resolved from the process roster, not a repo path -- R1)
read it as if it were current state, with no useful row bound::

    forensic_detail_api_v2.py  get_signal_scores()   ORDER BY signal_name
    search_api.py              /servers/{id}         ORDER BY score DESC
    ui_server.py               /api/servers/{id}     (no ORDER BY at all)
    approval_workflow.py       assessment summary    ORDER BY scored_at DESC LIMIT 12

The bus caps a result set at 200 rows. Measured LIVE 2026-10-01, the exact
statement the first three send returns::

    rows returned             200
    truncated                True    (the #3997 flag, live since 2026-09-28)
    DISTINCT signals shown      1     ['composite']
    actual distinct signals     12

So the forensic evidence pane, the search detail route and the UI detail route
were each showing ONE signal's rescore history and ZERO rows for the other
ELEVEN -- for 3,169 of 3,615 servers (88% are over the cap; 867 hold >10,000
rows). ``approval_workflow`` is bounded but wrong the same way: its 12 newest
rows by ``scored_at`` come from one rescore wave, so its dedup-by-name yields a
couple of signals rather than twelve.

THE CURE IS A BOUND BY CONSTRUCTION, NOT PAGINATION
---------------------------------------------------
Paging 12,834 rows into a detail pane would be correct and useless. What these
callers want is the LATEST row per ``signal_name``: 12 rows for that server and
**19 fleet-wide at worst** -- ``max(count(DISTINCT signal_name))`` over every
server, measured LIVE 2026-10-01 -- an order of magnitude under the 200-row cap,
so the result cannot be truncated at all. Verified LIVE: the statement below
returns 12 rows with ``truncated=false`` and all 12 signal names, against the
same server where the current statement returns 200 rows of one signal.

This is a module and not four edited SQL strings because #4003's "583 unbounded
row reads across 313 files" is the arithmetic of a hazard that had no
constructor. One door, four call sites, and a fifth caller gets it right by
importing rather than by reading a paragraph.

See also ``zo_sentinel/bus.py`` -- the paging door, for the reads that genuinely
do want every row.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from zo_sentinel import bus

__all__ = [
    "DEFAULT_COLUMNS",
    "MAX_OBSERVED_SIGNALS",
    "latest_signal_scores_sql",
    "latest_signal_scores",
]

DEFAULT_COLUMNS: Sequence[str] = (
    "server_id",
    "signal_name",
    "score",
    "evidence",
    "scored_at",
)

# max(count(DISTINCT signal_name)) over every server, measured LIVE 2026-10-01.
# Recorded so the margin against bus.OBSERVED_ROW_CAP is a number someone can
# re-measure, not a belief.
MAX_OBSERVED_SIGNALS = 19

_IDENT_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def _checked_columns(columns: Sequence[str]) -> Sequence[str]:
    """Refuse anything that is not a bare column identifier.

    The projection is interpolated into SQL (column names cannot be bound as
    parameters), so it is validated rather than trusted. ``server_id`` IS bound.
    """
    cols = tuple(columns)
    if not cols:
        raise ValueError("columns must not be empty")
    for col in cols:
        if not col or set(col) - _IDENT_CHARS:
            raise ValueError("not a bare column identifier: %r" % (col,))
    if "signal_name" not in cols:
        raise ValueError(
            "'signal_name' must be selected -- it is the partition key of the "
            "latest-per-signal read, and a caller that drops it cannot tell "
            "which signal a row belongs to"
        )
    return cols


def latest_signal_scores_sql(columns: Sequence[str] = DEFAULT_COLUMNS) -> str:
    """The statement, with ONE ``?`` placeholder for ``server_id``.

    One row per ``signal_name``, newest ``scored_at`` wins. Bounded by
    construction at the number of distinct signals (19 fleet-wide at worst),
    which is why it needs no pagination and cannot be silently capped.
    """
    cols = _checked_columns(columns)
    projected = ", ".join(cols)
    return (
        "SELECT %s FROM (SELECT %s, row_number() OVER ("
        "PARTITION BY signal_name ORDER BY scored_at DESC) AS _zo_rn "
        "FROM mcp_signal_scores WHERE server_id = ?) _zo_latest "
        "WHERE _zo_rn = 1 ORDER BY signal_name" % (projected, projected)
    )


def latest_signal_scores(
    server_id: str,
    columns: Sequence[str] = DEFAULT_COLUMNS,
    url: Optional[str] = None,
    timeout: float = bus.DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Current score for every signal of ``server_id`` -- one row per signal.

    Reads through ``bus.query``, so if this result were ever capped the bus's
    ``truncated`` flag turns it into a raised ``bus.BusTruncated`` instead of a
    short list that looks complete. With a width of 19 signals against a 200-row
    cap that branch is a tripwire, not an expected path -- which is the point:
    the caller is no longer the thing that has to notice.

    An empty ``server_id`` returns ``[]`` without a round trip. Everything else
    raises on failure: an unreadable bus is UNKNOWN, never zero signals (R6).
    """
    if not server_id:
        return []
    return bus.query(
        latest_signal_scores_sql(columns),
        params=[server_id],
        url=url,
        timeout=timeout,
    )
