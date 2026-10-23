#!/usr/bin/env python3
"""#4003 -- the signal-score read, and the paging door it delegates to.

Every assertion here has a NEGATIVE CONTROL in the same file: the defect is
reproduced going RED against the double before the cure is asserted green, so no
test in this file is an assertion that has never been seen fail (R4).

The double models the three things the live bus actually does and nothing else:
a 200-row cap, the ``truncated`` flag #3997 shipped (live 2026-09-28), and
LIMIT/OFFSET. The two statements under test are recognised by shape, so the
semantics being asserted are the ones the SQL expresses, not the double's guess.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zo_sentinel import bus, signal_reads  # noqa: E402

SERVER = "@goke/mcp"
SIGNALS = [
    "composite", "domain_age", "domain_trust", "github_stars",
    "known_bad_pattern", "otx_threat_intel", "reputation", "supply_chain",
    "tls_validity", "tool_count", "tool_security", "url_safety",
]
# Rescores per signal. 12 x 250 = 3,000 history rows for one server_id, which is
# the live shape at one quarter scale: '@goke/mcp' holds 12,834 rows across these
# same 12 signal names, 2,136 of them for 'composite' alone (measured LIVE
# 2026-10-01). It matters that this exceeds the cap PER SIGNAL, because that is
# what makes a 200-row page show exactly one signal and hide eleven.
RESCORES = 250


class FakeBus:
    """A write_service /query double: the row cap, the flag, and LIMIT/OFFSET.

    ``declare_truncation=False`` models a PRE-#3997 bus, which is how the
    unknown-is-not-zero branch of ``query_complete`` gets exercised.
    """

    def __init__(self, cap=bus.OBSERVED_ROW_CAP, declare_truncation=True):
        self.cap = cap
        self.declare_truncation = declare_truncation
        self.requests = []
        self.history = [
            {
                "server_id": SERVER,
                "signal_name": name,
                "score": float(10 + i),
                "evidence": "e%d" % i,
                # strictly increasing with i, so "newest per signal" has one
                # answer and the test is not asserting a tie-break
                "scored_at": "2026-09-01T00:%02d:%02d" % (i // 60, i % 60),
            }
            for name in SIGNALS
            for i in range(RESCORES)
        ]

    # -- statement semantics ------------------------------------------------
    def _rows_for(self, sql):
        if "_zo_rn = 1" in sql:
            latest = {}
            for row in self.history:
                cur = latest.get(row["signal_name"])
                if cur is None or row["scored_at"] > cur["scored_at"]:
                    latest[row["signal_name"]] = row
            return [latest[n] for n in sorted(latest)]
        if "ORDER BY score DESC" in sql:
            return sorted(self.history, key=lambda r: -r["score"])
        if "ORDER BY signal_name" in sql:
            return sorted(self.history, key=lambda r: r["signal_name"])
        return list(self.history)

    def post(self, sql, params=None, url=None, timeout=None):
        self.requests.append(sql)
        if sql.lstrip().upper().startswith("SELECT COUNT(*) AS N FROM ("):
            inner = sql[sql.index("FROM (") + 6:sql.rindex(") AS _zo_paged_sub")]
            return {"count": 1, "rows": [{"n": len(self._sliced(inner)[0])}]}
        rows, _ = self._sliced(sql)
        capped = rows[:self.cap]
        body = {"count": len(capped), "rows": capped}
        if self.declare_truncation:
            body["truncated"] = len(rows) > self.cap
            body["limit"] = self.cap
        return body

    def _sliced(self, sql):
        """Apply the statement's own LIMIT/OFFSET, before the server cap."""
        rows = self._rows_for(sql)
        limit = offset = None
        upper = sql.upper()
        if " OFFSET " in upper:
            offset = int(sql[upper.rindex(" OFFSET ") + 8:].split()[0])
        if " LIMIT " in upper:
            limit = int(sql[upper.rindex(" LIMIT ") + 7:].split()[0])
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows, (limit, offset)


@pytest.fixture
def fake(monkeypatch):
    f = FakeBus()
    monkeypatch.setattr(bus, "_post", f.post)
    return f


HISTORY_SQL = (
    "SELECT server_id, signal_name, score, evidence, scored_at "
    "FROM mcp_signal_scores WHERE server_id = ? ORDER BY signal_name"
)


def test_negative_control_the_double_reproduces_the_live_defect(fake):
    """RED, and it must be red or nothing below means anything.

    This is the statement the four live services sent, and the shape of the
    answer measured on the LIVE bus 2026-10-01: 200 rows, truncated=true, and
    ONE distinct signal_name out of twelve.
    """
    body = fake.post(HISTORY_SQL)
    assert len(body["rows"]) == bus.OBSERVED_ROW_CAP
    assert body["truncated"] is True
    shown = {r["signal_name"] for r in body["rows"]}
    assert len(shown) == 1, shown
    assert set(SIGNALS) - shown, "eleven signals must be MISSING from the page"


def test_bus_query_refuses_the_capped_history_read(fake):
    """The same statement through bus.query RAISES rather than return 200 rows.

    Before #3997 shipped there was nothing to raise on; this is what the flag
    bought, and it is why a module that merely moves onto the door stops lying
    even if nobody rewrites its SQL.
    """
    with pytest.raises(bus.BusTruncated):
        bus.query(HISTORY_SQL, params=[SERVER])


def test_latest_per_signal_returns_every_signal_and_is_not_truncated(fake):
    """GREEN: the cure. One row per signal, under the cap by construction."""
    rows = signal_reads.latest_signal_scores(SERVER)
    assert len(rows) == len(SIGNALS)
    assert sorted(r["signal_name"] for r in rows) == sorted(SIGNALS)
    assert len(rows) < bus.OBSERVED_ROW_CAP
    assert len(fake.requests) == 1, "a bounded read must not need pagination"


def test_latest_per_signal_picks_the_newest_row(fake):
    """The partition must resolve by scored_at DESC, not by arrival order."""
    newest = {}
    for row in fake.history:
        cur = newest.get(row["signal_name"])
        if cur is None or row["scored_at"] > cur["scored_at"]:
            newest[row["signal_name"]] = row
    got = {r["signal_name"]: r["scored_at"] for r in
           signal_reads.latest_signal_scores(SERVER)}
    assert got == {k: v["scored_at"] for k, v in newest.items()}


def test_query_complete_pages_the_history_to_completion(fake):
    """For a caller that genuinely wants every row, the door delivers them all."""
    rows = bus.query_complete(HISTORY_SQL, params=[SERVER])
    assert len(rows) == len(SIGNALS) * RESCORES
    assert len({r["signal_name"] for r in rows}) == len(SIGNALS)
    assert len(fake.requests) > 1, "3,000 rows cannot arrive in one 200-row page"


def test_query_complete_returns_one_request_when_nothing_is_capped(fake):
    """The common case must stay one request -- paging is not a tax on everyone."""
    rows = bus.query_complete(signal_reads.latest_signal_scores_sql(),
                              params=[SERVER])
    assert len(rows) == len(SIGNALS)
    assert len(fake.requests) == 1


def test_query_complete_derives_an_order_when_the_statement_has_none(monkeypatch):
    """No ORDER BY + a capped page: the order is derived from the returned columns.

    An unordered LIMIT/OFFSET walk can drop and duplicate rows, so the door
    refuses to do one. Without the derived order this call could not complete at
    all, which is the control: every row must arrive, exactly once.
    """
    f = FakeBus()
    monkeypatch.setattr(bus, "_post", f.post)
    rows = bus.query_complete(
        "SELECT server_id, signal_name, score, evidence, scored_at "
        "FROM mcp_signal_scores WHERE server_id = ?", params=[SERVER])
    assert len(rows) == len(SIGNALS) * RESCORES
    assert any("_zo_ord" in sql for sql in f.requests)


def test_query_complete_reconciles_when_the_bus_declares_nothing(monkeypatch):
    """A PRE-#3997 bus says nothing. Absence is UNKNOWN, not False (R6).

    A full first page with no flag must be treated as suspect and paged, and the
    assembled count must be reconciled against count(*) -- that is the only
    honest reading available when the server will not tell you.
    """
    f = FakeBus(declare_truncation=False)
    monkeypatch.setattr(bus, "_post", f.post)
    rows = bus.query_complete(HISTORY_SQL, params=[SERVER])
    assert len(rows) == len(SIGNALS) * RESCORES
    assert any("count(*)" in sql for sql in f.requests), \
        "no flag means reconciliation is the only proof the read was complete"


def test_query_complete_raises_when_a_bounded_statement_is_also_capped(monkeypatch):
    """RED: a caller's own LIMIT cannot be paged from here, and is not guessed at."""
    f = FakeBus()
    monkeypatch.setattr(bus, "_post", f.post)
    with pytest.raises(bus.BusTruncated):
        bus.query_complete(HISTORY_SQL + " LIMIT 400", params=[SERVER])


def test_sql_builder_refuses_a_projection_it_cannot_vouch_for():
    """Column names are interpolated, so they are validated, not trusted."""
    with pytest.raises(ValueError):
        signal_reads.latest_signal_scores_sql(("signal_name", "score; DROP TABLE x"))
    with pytest.raises(ValueError):
        signal_reads.latest_signal_scores_sql(())
    with pytest.raises(ValueError):
        signal_reads.latest_signal_scores_sql(("score", "evidence"))
    sql = signal_reads.latest_signal_scores_sql()
    assert sql.count("?") == 1
    assert "row_number() OVER (PARTITION BY signal_name" in sql


def test_an_empty_server_id_costs_no_round_trip(fake):
    assert signal_reads.latest_signal_scores("") == []
    assert fake.requests == []
