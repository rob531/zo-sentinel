"""#4003 (G6): the bus READ CONSTRUCTOR, and the defect it closes in #4662's fix.

MEASURED ON THE LIVE RUNTIME 2026-09-28 (not hypothetical, and re-measured 24
days after #4662 merged):

    response keys                           ['count', 'rows']   -- still NO flag
    unpaginated information_schema.columns   200 rows
    count(*) over the same predicate         373
    information_schema.tables                46   (was 44 when #4003 was filed)
    SELECT count(*) FROM (<that stmt>) AS s  373  -- the derived count works

THE NEGATIVE CONTROL (HARNESS_DOCTRINE R4)
------------------------------------------
``_short_page_only`` below is #4662's algorithm reproduced verbatim: page, stop
on the first SHORT page, no reconciliation, page size == the observed cap (200).

``TestShortPageOnlyIsUnsoundWhenTheCapMoves`` drives it against a bus whose cap
is 100 -- LOWER than the page size. Every page comes back short, the loop stops
after one request, and it returns 100 of 373 while reporting success. That is
the original silent-truncation bug wearing the fix's clothes, and it is observed
RED here before ``query_all`` is believed. An assertion never seen fail is not
evidence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel import bus  # noqa: E402

CAP = 200
TOTAL = 373

SQL = (
    "SELECT table_name, column_name, data_type FROM information_schema.columns "
    "WHERE table_schema='main' ORDER BY table_name, ordinal_position"
)


def _corpus(total: int = TOTAL):
    return [
        {
            "table_name": "t%03d" % (i // 10),
            "column_name": "c%03d" % (i % 10),
            "data_type": "VARCHAR",
        }
        for i in range(total)
    ]


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class FakeBus:
    """A /query endpoint that silently truncates to ``cap`` rows, as measured.

    Honours LIMIT/OFFSET, then applies the cap on top -- so a caller that does
    not page sees exactly ``cap`` rows and no indication that more exist.

    It also answers ``count(*)``, because the real bus does (verified live
    2026-09-28) and because a fixture that cannot be asked "how many are there"
    cannot model the only question that distinguishes complete from capped.
    """

    def __init__(self, rows=None, cap: int = CAP, truncated_flag=None):
        self.rows = _corpus() if rows is None else rows
        self.cap = cap
        self.truncated_flag = truncated_flag
        self.calls: list[str] = []
        self.count_calls = 0

    def __call__(self, url, json=None, timeout=None, **kw):
        sql = (json or {}).get("sql", "")
        self.calls.append(sql)

        if "count(*)" in sql.lower():
            self.count_calls += 1
            return _Resp({"rows": [{"n": len(self.rows)}], "count": 1})

        m = re.search(r"LIMIT\s+(\d+)(?:\s+OFFSET\s+(\d+))?", sql, re.IGNORECASE)
        out = self.rows
        if m:
            limit = int(m.group(1))
            offset = int(m.group(2) or 0)
            out = out[offset:offset + limit]
        out = out[: self.cap]
        body = {"rows": out, "count": len(out)}
        if self.truncated_flag is not None:
            body["truncated"] = self.truncated_flag
        return _Resp(body)

    @property
    def data_calls(self) -> int:
        return len(self.calls) - self.count_calls


def _short_page_only(post, page_rows=CAP, max_pages=200):
    """#4662's algorithm, reproduced verbatim. The object under negative control."""
    rows = []
    for page in range(max_pages):
        resp = post(
            "http://x/query",
            json={
                "sql": "%s LIMIT %d OFFSET %d"
                % (SQL, page_rows, page * page_rows)
            },
            timeout=30,
        )
        resp.raise_for_status()
        got = resp.json().get("rows") or []
        rows.extend(got)
        if len(got) < page_rows:
            return rows
    raise RuntimeError("paging exceeded %d pages" % max_pages)


# --------------------------------------------------------------------------
# The baseline defect, restated so the fix has something to be better than
# --------------------------------------------------------------------------


class TestTheCapIsRealAndSilent:
    def test_a_single_request_sees_only_the_cap(self, monkeypatch):
        fake = FakeBus()
        monkeypatch.setattr(bus.requests, "post", fake)
        rows = bus.query(SQL)
        assert len(rows) == CAP, "the fixture no longer reproduces the cap"
        assert len(rows) < TOTAL

    def test_the_count_field_cannot_flag_truncation(self):
        """`count` == len(rows) on every query, so it is not a signal."""
        fake = FakeBus()
        capped = fake("u", json={"sql": SQL}).json()
        bounded = fake("u", json={"sql": SQL + " LIMIT 7"}).json()
        assert capped["count"] == len(capped["rows"]) == CAP
        assert bounded["count"] == len(bounded["rows"]) == 7


# --------------------------------------------------------------------------
# NEGATIVE CONTROL: the prior design, observed returning a partial answer
# --------------------------------------------------------------------------


class TestShortPageOnlyIsUnsoundWhenTheCapMoves:
    def test_prior_design_returns_a_partial_answer_and_calls_it_complete(self):
        fake = FakeBus(cap=100)
        got = _short_page_only(fake)
        assert len(got) == 100, "expected the prior design to stop after one page"
        assert len(got) != TOTAL
        assert fake.data_calls == 1, (
            "it stopped after ONE request and reported success -- this is the "
            "silent truncation the fix was supposed to end"
        )

    def test_the_constructor_refuses_the_same_situation(self, monkeypatch):
        fake = FakeBus(cap=100)
        monkeypatch.setattr(bus.requests, "post", fake)
        with pytest.raises(bus.BusPaginationError, match="pagination mismatch"):
            bus.query_all(SQL, page_rows=CAP)

    def test_page_size_at_or_above_the_cap_without_reconciliation_is_refused(self):
        with pytest.raises(ValueError, match="observed server cap"):
            bus.query_all(SQL, page_rows=CAP, reconcile=False)


# --------------------------------------------------------------------------
# The constructor
# --------------------------------------------------------------------------


class TestQueryAll:
    def test_returns_every_row(self, monkeypatch):
        fake = FakeBus()
        monkeypatch.setattr(bus.requests, "post", fake)
        rows = bus.query_all(SQL)
        assert len(rows) == TOTAL
        assert rows[-1]["table_name"] == "t037"
        assert fake.count_calls == 1, "reconciliation must actually ask the bus"

    def test_default_page_size_is_below_the_observed_cap(self):
        assert bus.DEFAULT_PAGE_ROWS < bus.OBSERVED_ROW_CAP

    def test_empty_relation_terminates_in_one_data_request(self, monkeypatch):
        fake = FakeBus(rows=[])
        monkeypatch.setattr(bus.requests, "post", fake)
        assert bus.query_all(SQL) == []
        assert fake.data_calls == 1

    def test_exactly_one_full_page_still_asks_for_a_second(self, monkeypatch):
        fake = FakeBus(rows=_corpus(bus.DEFAULT_PAGE_ROWS))
        monkeypatch.setattr(bus.requests, "post", fake)
        rows = bus.query_all(SQL)
        assert len(rows) == bus.DEFAULT_PAGE_ROWS
        assert fake.data_calls == 2, (
            "a full-length page is indistinguishable from a capped one"
        )

    def test_refuses_a_statement_without_a_total_order(self, monkeypatch):
        monkeypatch.setattr(bus.requests, "post", FakeBus())
        with pytest.raises(ValueError, match="total order"):
            bus.query_all("SELECT a FROM t")

    def test_unordered_ok_is_an_explicit_opt_in(self, monkeypatch):
        fake = FakeBus(rows=_corpus(10))
        monkeypatch.setattr(bus.requests, "post", fake)
        assert len(bus.query_all("SELECT a FROM t", unordered_ok=True)) == 10

    def test_refuses_a_statement_that_already_pages(self, monkeypatch):
        monkeypatch.setattr(bus.requests, "post", FakeBus())
        with pytest.raises(ValueError, match="must not carry them"):
            bus.query_all(SQL + " LIMIT 10")
        with pytest.raises(ValueError, match="must not carry them"):
            bus.query_all(SQL + " OFFSET 10")

    def test_refuses_to_loop_forever_when_offset_is_ignored(self, monkeypatch):
        class OffsetIgnoring(FakeBus):
            def __call__(self, url, json=None, timeout=None, **kw):
                sql = re.sub(
                    r"\s*OFFSET\s+\d+", "", (json or {}).get("sql", "")
                )
                return super().__call__(url, json={"sql": sql}, timeout=timeout)

        monkeypatch.setattr(bus.requests, "post", OffsetIgnoring())
        with pytest.raises(RuntimeError, match="paging exceeded"):
            bus.query_all(SQL, max_pages=3)

    def test_reconcile_off_is_allowed_below_the_cap(self, monkeypatch):
        fake = FakeBus()
        monkeypatch.setattr(bus.requests, "post", fake)
        rows = bus.query_all(SQL, reconcile=False)
        assert len(rows) == TOTAL
        assert fake.count_calls == 0

    def test_caller_supplied_count_sql_is_used(self, monkeypatch):
        fake = FakeBus()
        monkeypatch.setattr(bus.requests, "post", fake)
        rows = bus.query_all(
            SQL,
            count_sql="SELECT count(*) AS n FROM information_schema.columns",
        )
        assert len(rows) == TOTAL
        assert fake.count_calls == 1


class TestTruncationFlagForwardCompatibility:
    def test_a_declared_truncation_raises_instead_of_returning_short(
        self, monkeypatch
    ):
        """#3997 becomes an improvement, not a precondition for 583 callers."""
        monkeypatch.setattr(bus.requests, "post", FakeBus(truncated_flag=True))
        with pytest.raises(bus.BusTruncated):
            bus.query(SQL)

    def test_a_declared_non_truncation_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(bus.requests, "post", FakeBus(truncated_flag=False))
        assert len(bus.query(SQL)) == CAP

    def test_absence_of_the_flag_is_unknown_not_false(self):
        assert bus._declared_truncation({"rows": [], "count": 0}) is None
        assert bus._declared_truncation({"truncated": False}) is False
        assert bus._declared_truncation({"truncated": True}) is True


class TestCountOf:
    def test_reads_n(self, monkeypatch):
        monkeypatch.setattr(bus.requests, "post", FakeBus())
        assert bus.count_of(SQL) == TOTAL

    def test_a_count_with_no_rows_raises(self, monkeypatch):
        class NoRows(FakeBus):
            def __call__(self, url, json=None, timeout=None, **kw):
                return _Resp({"rows": [], "count": 0})

        monkeypatch.setattr(bus.requests, "post", NoRows())
        with pytest.raises(bus.BusError, match="no rows"):
            bus.count_of(SQL)

    def test_an_ambiguous_count_shape_raises_rather_than_guess(self, monkeypatch):
        class Ambiguous(FakeBus):
            def __call__(self, url, json=None, timeout=None, **kw):
                return _Resp({"rows": [{"a": 1, "b": 2}], "count": 1})

        monkeypatch.setattr(bus.requests, "post", Ambiguous())
        with pytest.raises(bus.BusError, match="name the count column"):
            bus.count_of(SQL)


class TestTransportFailuresAreLoud:
    def test_a_transport_error_is_never_an_empty_result(self, monkeypatch):
        def boom(*a, **kw):
            raise OSError("connection refused")

        monkeypatch.setattr(bus.requests, "post", boom)
        with pytest.raises(bus.BusError, match="OSError"):
            bus.query(SQL)


class TestQueryUrl:
    def test_env_decides(self, monkeypatch):
        monkeypatch.setenv("ZO_WRITE_SERVICE", "http://example.invalid:9999/")
        assert bus.query_url() == "http://example.invalid:9999/query"

    def test_explicit_url_wins(self, monkeypatch):
        monkeypatch.setenv("ZO_WRITE_SERVICE", "http://example.invalid:9999")
        assert bus.query_url("http://other/query") == "http://other/query"
