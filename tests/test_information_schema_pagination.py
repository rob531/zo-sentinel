"""#4003 (G6): the two information_schema readers must page past the bus row cap.

MEASURED ON THE LIVE RUNTIME 2026-09-04 (this is not a hypothetical):

    RESPONSE_KEYS ['count', 'rows']
    RESPONSE_COUNT_FIELD 200
    ROWS_RETURNED     200
    LAST_TABLE_SEEN   mcp_ecosystems_metadata
    ROWS_ACTUAL       [{'n': 373}]
    PAGE2_ROWS        173      (LIMIT 200 OFFSET 200)
    PAGE2_FIRST_TABLE mcp_exemptions
    PAGE2_LAST_TABLE  write_queue_log

Two facts follow, and both are load-bearing here:

1. The cap is REAL and SILENT. 173 of 373 columns were invisible to a schema
   *drift* probe and to the generator of the committed schema doc. A probe that
   cannot see a region does not report "unknown" for it -- it reports "no
   drift", which is the harness-doctrine failure this repo keeps paying for.
2. Plain ``LIMIT/OFFSET`` pages past it. So this fix does NOT depend on #3997's
   ``truncated`` flag, and #4003's "blocked on #3997" does not hold for these
   two callers. The response's ``count`` field is len(rows) on every query (7
   for a 7-row read, 200 for the capped read) and therefore cannot flag
   truncation -- which is exactly why the terminating condition below is "a
   page came back SHORT" rather than "the bus said it truncated".

NEGATIVE CONTROL (HARNESS_DOCTRINE R4): ``FakeCappedBus`` reproduces the cap
above. Against the pre-fix single-request readers these tests FAIL, returning
200 of 373 -- observed RED before the fix was believed. An assertion never seen
fail is not evidence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import refresh_schema_doc  # noqa: E402
from zo_sentinel.probes import duckdb_schema_uptime_probe as probe  # noqa: E402

CAP = 200
TOTAL = 373


def _corpus(total: int = TOTAL):
    """`total` column rows over enough tables to straddle the cap.

    Table names are zero-padded so lexical order matches generation order, the
    way `ORDER BY table_name, ordinal_position` behaves on the real bus.
    """
    return [
        {
            "table_name": "t%03d" % (i // 10),
            "column_name": "c%03d" % (i % 10),
            "data_type": "VARCHAR",
        }
        for i in range(total)
    ]


class FakeCappedBus:
    """A /query endpoint that silently truncates to CAP rows, as measured.

    Honours LIMIT/OFFSET, then applies the cap on top -- so a caller that does
    not page sees exactly CAP rows and no indication that more exist.
    """

    def __init__(self, rows=None, cap: int = CAP):
        self.rows = _corpus() if rows is None else rows
        self.cap = cap
        self.calls: list[str] = []

    def __call__(self, url, json=None, timeout=None, **kw):
        sql = (json or {}).get("sql", "")
        self.calls.append(sql)

        limit = offset = None
        m = re.search(r"\bLIMIT\s+(\d+)", sql, re.I)
        if m:
            limit = int(m.group(1))
        m = re.search(r"\bOFFSET\s+(\d+)", sql, re.I)
        if m:
            offset = int(m.group(1))

        out = self.rows[offset or 0:]
        if limit is not None:
            out = out[:limit]
        out = out[: self.cap]  # the silent cap, applied last

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        # `count` mirrors len(rows) exactly, as observed -- deliberately NOT a
        # truncation signal, so a test cannot accidentally lean on one.
        resp.json = MagicMock(return_value={"rows": out, "count": len(out)})
        return resp


class TestProbePagesPastCap:
    def test_returns_every_row_not_just_the_first_page(self):
        bus = FakeCappedBus()
        with patch.object(probe.requests, "post", new=bus):
            rows = probe.fetch_live_duckdb_columns()
        assert len(rows) == TOTAL, (
            "read %d of %d columns -- the tail of the schema is invisible to "
            "the drift probe" % (len(rows), TOTAL)
        )
        assert len(bus.calls) > 1, "one request cannot exceed the cap"

    def test_last_table_beyond_the_cap_is_present(self):
        bus = FakeCappedBus()
        with patch.object(probe.requests, "post", new=bus):
            rows = probe.fetch_live_duckdb_columns()
        # TOTAL-1 = 372 -> 372 // 10 = 37; the pre-fix reader stopped at t019.
        assert rows[-1]["table_name"] == "t037"
        assert any(r["table_name"] == "t030" for r in rows)

    def test_exactly_one_full_page_still_asks_for_a_second(self):
        """A page of exactly CAP is indistinguishable from a capped one."""
        bus = FakeCappedBus(rows=_corpus(CAP))
        with patch.object(probe.requests, "post", new=bus):
            rows = probe.fetch_live_duckdb_columns()
        assert len(rows) == CAP
        assert len(bus.calls) == 2, "must confirm the cap was not hit"

    def test_empty_schema_terminates(self):
        bus = FakeCappedBus(rows=[])
        with patch.object(probe.requests, "post", new=bus):
            rows = probe.fetch_live_duckdb_columns()
        assert rows == []
        assert len(bus.calls) == 1

    def test_skip_tables_filter_still_applies(self):
        rows_in = _corpus() + [
            {"table_name": "zz_skip", "column_name": "c", "data_type": "VARCHAR"}
        ]
        bus = FakeCappedBus(rows=rows_in)
        with patch.object(probe, "SKIP_TABLES", {"zz_skip"}):
            with patch.object(probe.requests, "post", new=bus):
                rows = probe.fetch_live_duckdb_columns()
        assert len(rows) == TOTAL
        assert all(r["table_name"] != "zz_skip" for r in rows)

    def test_refuses_to_loop_forever_if_offset_is_ignored(self):
        """A bus that ignores OFFSET would page for ever. Raise, don't spin."""

        class OffsetIgnoringBus(FakeCappedBus):
            def __call__(self, url, json=None, timeout=None, **kw):
                sql = re.sub(r"\s*OFFSET\s+\d+", "", (json or {}).get("sql", ""))
                return super().__call__(url, json={"sql": sql}, timeout=timeout)

        bus = OffsetIgnoringBus()
        with patch.object(probe, "BUS_MAX_PAGES", 3):
            with patch.object(probe.requests, "post", new=bus):
                with pytest.raises(RuntimeError, match="paging exceeded"):
                    probe.fetch_live_duckdb_columns()


class TestRefreshSchemaDocPagesPastCap:
    def test_returns_every_row_not_just_the_first_page(self):
        bus = FakeCappedBus()
        with patch.object(refresh_schema_doc.requests, "post", new=bus):
            rows = refresh_schema_doc.fetch_duckdb_rows()
        assert len(rows) == TOTAL, (
            "the committed schema doc would be generated from %d of %d columns"
            % (len(rows), TOTAL)
        )
        assert len(bus.calls) > 1

    def test_hash_differs_between_full_and_truncated_read(self):
        """The point of the bug: a partial read produced a confident hash."""
        full = refresh_schema_doc.compute_schema_hash(_corpus())
        capped = refresh_schema_doc.compute_schema_hash(_corpus()[:CAP])
        assert full != capped

        bus = FakeCappedBus()
        with patch.object(refresh_schema_doc.requests, "post", new=bus):
            rows = refresh_schema_doc.fetch_duckdb_rows()
        assert refresh_schema_doc.compute_schema_hash(rows) == full
