"""Negative controls for bus_write_guard (improvement-loop cycle-0181).

HARNESS_DOCTRINE R4: an assertion never observed RED is an untested branch, not
evidence.  Every pole below was run against the PRE-cure tree first; the ones marked
PRE-CURE RED in their docstring failed there and pass here.  The ones that pass in both
directions are over-firing poles and are left honest rather than contrived into failing.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bus_write_guard as g  # noqa: E402


# --------------------------------------------------------------------------- helpers

def querier_rows(rows):
    def _q(url, sql, timeout):
        return {"rows": rows}
    return _q


def querier_raises(exc=ConnectionError("connection reset by peer")):
    def _q(url, sql, timeout):
        raise exc
    return _q


class RecordingPoster:
    def __init__(self, succeed_first=None):
        self.calls = []
        self.succeed_first = succeed_first

    def __call__(self, write_url, table, row, timeout):
        self.calls.append(row)
        if self.succeed_first is None:
            return True
        return len(self.calls) <= self.succeed_first


@pytest.fixture(autouse=True)
def _clear_cache():
    g.reset_cache()
    yield
    g.reset_cache()


# ------------------------------------------------------------------ resolve_table

def test_absent_table_is_absent_not_present():
    state, basis = g.resolve_table("nope", "http://bus/query", querier=querier_rows([]), use_cache=False)
    assert state == g.ABSENT
    assert "none named nope" in basis


def test_unreachable_bus_is_unknown_never_absent():
    """PRE-CURE RED by construction: the code this replaces had no UNKNOWN state at all.

    This is the dangerous direction.  A bus restart must not be read as "the table was
    deleted" -- write_service restarted five times on 2026-10-04.
    """
    state, basis = g.resolve_table("t", "http://bus/query", querier=querier_raises(), use_cache=False)
    assert state == g.UNKNOWN
    assert state != g.ABSENT
    assert "unreachable" in basis


def test_malformed_answer_is_unknown():
    state, _ = g.resolve_table("t", "http://bus/query", querier=lambda u, s, t: {"count": 0}, use_cache=False)
    assert state == g.UNKNOWN


def test_non_dict_answer_is_unknown():
    state, _ = g.resolve_table("t", "http://bus/query", querier=lambda u, s, t: ["t"], use_cache=False)
    assert state == g.UNKNOWN


def test_present_table_is_present():
    state, basis = g.resolve_table(
        "mcp_server_registry",
        "http://bus/query",
        querier=querier_rows([{"table_name": "mcp_server_registry"}]),
        use_cache=False,
    )
    assert state == g.PRESENT
    assert "returned the row" in basis


def test_empty_columns_read_would_have_passed_the_old_check():
    """The exact 2026-10-05 defect, stated as a test.

    `information_schema.columns` for an absent table answers HTTP 200 with rows=[].
    discovery_github_paginator cached that [] and set SCHEMA_VERIFIED = True.  Asking
    information_schema.TABLES for the table's own row cannot be fooled the same way.
    """
    cols_answer = {"rows": []}          # what .columns says for an absent table
    tables_answer = {"rows": []}        # what .tables says for an absent table
    assert cols_answer.get("rows") == []            # old code: truthy-empty -> "verified"
    state, _ = g.resolve_table("absent_tbl", "http://bus/query",
                               querier=lambda u, s, t: tables_answer, use_cache=False)
    assert state == g.ABSENT


# ------------------------------------------------------------------ guarded_write

def test_absent_target_posts_nothing_and_spools_everything(tmp_path):
    """NEGATIVE CONTROL, the headline one.

    PRE-CURE this is RED: discovery_github_paginator.write_repos posted all three rows,
    collected three 2xx enqueue receipts and returned written=3 while write_service
    logged `Catalog Error` for each.  Here: posted 0, spooled 3, poster never called.
    """
    poster = RecordingPoster()
    rows = [{"candidate_name": f"n{i}"} for i in range(3)]
    res = g.guarded_write(
        "mcp_discovery_candidates", rows,
        write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_rows([]), poster=poster,
        logger=lambda m: None, use_cache=False,
    )
    assert res.state == g.ABSENT
    assert res.posted == 0
    assert res.spooled == 3
    assert poster.calls == []          # nothing reached the bus
    assert not res.ok
    spool = tmp_path / "mcp_discovery_candidates.jsonl"
    assert spool.exists() and len(spool.read_text().strip().splitlines()) == 3


def test_unknown_target_also_posts_nothing_but_keeps_the_rows(tmp_path):
    poster = RecordingPoster()
    res = g.guarded_write(
        "t", [{"a": 1}],
        write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_raises(), poster=poster,
        logger=lambda m: None, use_cache=False,
    )
    assert res.state == g.UNKNOWN
    assert res.posted == 0 and res.spooled == 1
    assert poster.calls == []


def test_present_target_posts_normally(tmp_path):
    poster = RecordingPoster()
    rows = [{"a": 1}, {"a": 2}]
    res = g.guarded_write(
        "t", rows,
        write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_rows([{"table_name": "t"}]), poster=poster,
        logger=lambda m: None, use_cache=False,
    )
    assert res.state == g.PRESENT and res.posted == 2 and res.spooled == 0
    assert res.ok and len(poster.calls) == 2


def test_partial_post_failure_spools_the_remainder(tmp_path):
    poster = RecordingPoster(succeed_first=1)
    res = g.guarded_write(
        "t", [{"a": 1}, {"a": 2}, {"a": 3}],
        write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_rows([{"table_name": "t"}]), poster=poster,
        logger=lambda m: None, use_cache=False,
    )
    assert res.posted == 1 and res.failed == 2 and res.spooled == 2
    assert not res.ok


def test_empty_batch_posts_nothing_and_does_not_create_a_spool(tmp_path):
    """Over-firing pole: a quiet cycle must stay quiet.  Passes in both directions."""
    poster = RecordingPoster()
    res = g.guarded_write(
        "t", [],
        write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_rows([]), poster=poster,
        logger=lambda m: None, use_cache=False,
    )
    assert res.posted == 0 and res.spooled == 0 and poster.calls == []
    assert not (tmp_path / "t.jsonl").exists()


# ------------------------------------------------------------------ spool + replay

def test_spool_is_idempotent_for_the_same_rows(tmp_path):
    rows = [{"a": 1}, {"a": 2}]
    g.spool_rows("t", rows, str(tmp_path), "ABSENT")
    g.spool_rows("t", rows, str(tmp_path), "ABSENT")
    assert g.spool_depth("t", str(tmp_path)) == 2


def test_replay_leaves_the_spool_alone_while_the_target_is_absent(tmp_path):
    g.spool_rows("t", [{"a": 1}], str(tmp_path), "ABSENT")
    poster = RecordingPoster()
    res = g.replay_spool(
        "t", write_url="http://bus/write", query_url="http://bus/query",
        spool_dir=str(tmp_path), querier=querier_rows([]), poster=poster,
        logger=lambda m: None,
    )
    assert res.state == g.ABSENT and res.posted == 0 and poster.calls == []
    assert g.spool_depth("t", str(tmp_path)) == 1


def test_replay_drains_once_present_and_is_idempotent(tmp_path):
    g.spool_rows("t", [{"a": 1}, {"a": 2}], str(tmp_path), "ABSENT")
    poster = RecordingPoster()
    q = querier_rows([{"table_name": "t"}])
    first = g.replay_spool("t", write_url="http://bus/write", query_url="http://bus/query",
                           spool_dir=str(tmp_path), querier=q, poster=poster, logger=lambda m: None)
    assert first.posted == 2 and g.spool_depth("t", str(tmp_path)) == 0
    second = g.replay_spool("t", write_url="http://bus/write", query_url="http://bus/query",
                            spool_dir=str(tmp_path), querier=q, poster=poster, logger=lambda m: None)
    assert second.posted == 0           # idempotent: re-running changes nothing
    assert len(poster.calls) == 2


def test_replay_keeps_rows_the_bus_refused(tmp_path):
    g.spool_rows("t", [{"a": 1}, {"a": 2}], str(tmp_path), "ABSENT")
    poster = RecordingPoster(succeed_first=1)
    res = g.replay_spool("t", write_url="http://bus/write", query_url="http://bus/query",
                         spool_dir=str(tmp_path), querier=querier_rows([{"table_name": "t"}]),
                         poster=poster, logger=lambda m: None)
    assert res.posted == 1 and res.failed == 1
    assert g.spool_depth("t", str(tmp_path)) == 1


# --------------------------------------------- the live daemon, not just the helper

def test_github_paginator_reports_zero_when_the_table_is_absent(tmp_path, monkeypatch):
    """PRE-CURE RED: write_repos returned (3, 0) against an absent table.

    This drives the real daemon function, not a copy of its logic -- R1 in the small.
    """
    import discovery_github_paginator as dgp

    monkeypatch.setattr(dgp, "SPOOL_DIR", str(tmp_path))
    monkeypatch.setattr(g, "_post_query", lambda u, s, t: {"rows": []})
    posted = []
    monkeypatch.setattr(g, "_post_write", lambda u, tb, r, to: posted.append(r) or True)
    g.reset_cache()

    items = [{"full_name": f"o/r{i}", "html_url": "u", "description": "d"} for i in range(3)]
    written, errors = dgp.write_repos(items)
    assert written == 0, "an absent table must never be reported as written rows"
    assert errors == 3
    assert posted == [], "not one row may reach /write when the target is absent"


def test_github_paginator_still_writes_when_the_table_is_present(tmp_path, monkeypatch):
    import discovery_github_paginator as dgp

    monkeypatch.setattr(dgp, "SPOOL_DIR", str(tmp_path))
    monkeypatch.setattr(g, "_post_query", lambda u, s, t: {"rows": [{"table_name": "mcp_discovery_candidates"}]})
    posted = []
    monkeypatch.setattr(g, "_post_write", lambda u, tb, r, to: posted.append(r) or True)
    g.reset_cache()

    items = [{"full_name": "o/r", "html_url": "u", "description": "d"}]
    written, errors = dgp.write_repos(items)
    assert (written, errors) == (1, 0)
    assert len(posted) == 1
