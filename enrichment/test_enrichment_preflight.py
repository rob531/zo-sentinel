#!/usr/bin/env python3
"""
Regression tests for the enrichment preflight gate.

Each test pins a way the PREVIOUS audit failed open. The point of this file is
that a check which cannot run must read as BLOCKED, never as "nothing found".

Run: python3 test_enrichment_preflight.py
"""
from __future__ import annotations

import sys

import enrichment_preflight as ep

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        FAILED.append(f"{name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
    else:
        PASSED.append(name)


def _raises(fn, needle: str) -> None:
    try:
        fn()
    except ep.PreflightError as exc:
        assert needle.lower() in str(exc).lower(), f"expected {needle!r} in {exc!r}"
        return
    raise AssertionError(f"expected PreflightError containing {needle!r}, got none")


# --- the exact defects that let url_safety survive eight 'fix' cycles --------

def test_malformed_sql_raises_not_empty():
    """Every malformed-query shape must raise, never return an empty list.

    Measured, not assumed: the WriteService answers an unknown column (binder
    error), a syntax error (parser error) and an unknown table all with HTTP
    400 and a `detail` body. The old audit wrapped its request in
    `except RequestException: return None`, so all three arrived at
    analyze_signal_quality(None) -> {'distinct_scores': 0} and read as a clean
    bill of health. Each is pinned here.
    """
    cases = {
        "unknown column": "SELECT score_value, COUNT(*) AS cnt FROM mcp_signal_scores "
                          "WHERE signal_type='url_safety' GROUP BY score_value",
        "syntax error": "SELECT MIN(scored_at) first FROM mcp_signal_scores",
        "unknown table": "SELECT 1 FROM no_such_table_xyz",
    }
    for label, sql in cases.items():
        try:
            rows = ep.query(sql)
        except ep.PreflightError:
            continue
        raise AssertionError(f"{label}: returned {rows!r} instead of raising")


def test_detail_body_at_http_200_also_raises():
    """Defence in depth: a `detail` body is rejected even at HTTP 200, so the
    guard does not depend on the service's choice of status code."""
    import unittest.mock as mock

    fake = mock.Mock(status_code=200)
    fake.json.return_value = {"detail": "Parser Error: something"}
    with mock.patch.object(ep.requests, "post", return_value=fake):
        _raises(lambda: ep.query("SELECT 1"), "SQL rejected")

    fake2 = mock.Mock(status_code=200)
    fake2.json.return_value = {"data": []}          # the key the old audit read
    with mock.patch.object(ep.requests, "post", return_value=fake2):
        _raises(lambda: ep.query("SELECT 1"), "no 'rows' key")


def test_phantom_column_blocks_before_query():
    """Column existence is asserted up front, so a hallucinated name cannot
    reach the database and come back as an absence of data."""
    _raises(
        lambda: ep.assert_columns("mcp_signal_scores", ["signal_name", "mcp_request_id"]),
        "missing referenced column",
    )


def test_missing_table_blocks():
    _raises(lambda: ep.assert_columns("table_that_does_not_exist", ["x"]), "does not exist")


def test_real_columns_pass():
    ep.assert_columns("mcp_signal_scores", ["server_id", "signal_name", "score", "scored_at"])


# --- the gate itself --------------------------------------------------------

def test_blocks_degenerate_population():
    """Four hostnames behind 3,189 servers must not arm a metered vendor."""
    v = ep.preflight(
        source="test",
        population_sql=ep.REGISTRY_POPULATION,
        join_key_expr=ep.HOST_KEY,
        signal_name="__test__",
        monthly_budget=25_000,
        credits_available=25_000,
    )
    assert not v.armed, "gate armed on a 4-key population"
    names = {c.name for c in v.blockers}
    assert "key_cardinality" in names, f"cardinality not flagged; blockers={names}"


def test_arms_on_discriminating_population():
    """The gate must not simply always block -- that is a different constant."""
    synthetic = (
        "SELECT 'srv-' || CAST(i AS VARCHAR) AS server_id, "
        "'https://host-' || CAST(i AS VARCHAR) || '.example.net/mcp' AS url "
        "FROM range(1, 200) t(i)"
    )
    checks = ep.check_discrimination(synthetic, ep.HOST_KEY)
    assert all(c.passed for c in checks), \
        f"discrimination checks failed on a clean population: {[c.name for c in checks if not c.passed]}"


def test_zero_credits_blocks():
    v = ep.preflight(
        source="test",
        population_sql=ep.REGISTRY_POPULATION,
        join_key_expr=ep.HOST_KEY,
        signal_name="__test__",
        monthly_budget=25_000,
        credits_available=0,
    )
    assert any(c.name == "credits" and not c.passed for c in v.checks), "zero credits did not block"


def test_undeclared_budget_blocks():
    """No declared budget is a block, not a default-allow."""
    c = ep.check_budget("url_safety", population=3189, cost_per_lookup=1, monthly_budget=None)
    assert not c.passed, "undeclared budget was allowed"


def test_budget_detects_real_rescore_cadence():
    """The cost model reads the cadence off the table rather than assuming one
    pass per day. url_safety is rewritten many times daily."""
    c = ep.check_budget("url_safety", population=3189, cost_per_lookup=1, monthly_budget=25_000)
    assert c.evidence["rescore_factor_per_day"] > 1.0, \
        f"rescore factor not detected: {c.evidence}"
    assert not c.passed, "a multi-thousand-percent budget overrun was allowed"


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)

    for n in PASSED:
        print(f"  [PASS] {n}")
    for f in FAILED:
        print(f"  [FAIL] {f}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
