#!/usr/bin/env python3
"""Guarantees for the FU -> code-subgraph accessor.

Every test here corresponds to a way this tool could silently lie, and the two
that matter most are the ones that guard against the failures that CREATED it:

  * `test_unknown_fu_cannot_evaluate` -- a probe that cannot evaluate must not
    report success. FU-103 was recorded green while its artifact did not exist.
  * `test_bus_unreachable_still_exits_zero` -- the tool must degrade, not error,
    when the :8772 bus is unreachable, because a step-6 instruction that errors
    is what sent 13 lanes back to grep for four days.

`test_out_of_scope_is_not_called_stale` guards the third: an anchor the KL was
never going to index is NOT breakage, and conflating the two produced two false
reds during the 2026-07-29 link audit.

Negative control: see `--negative-control` in the docstring of the mutant runner
below. These assertions were each observed RED against a deliberately broken
build before being trusted.
"""
import io
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fu_context  # noqa: E402

LEDGER_SAMPLE = """# Follow-ups

### FU-777 | A sample entry that names some anchors
- date: 2026-07-30 - source: test - status: open - priority: P2
- class: defect
- detail: touches weekly_rescore.py and tools/rescore/score_validity.py plus
  a duplicate D:\\zo\\_runbook\\tools\\rescore\\weekly_rescore.py and go.sh, a
  config schemas/risk_axis_mapping_v1.json, and a path with a SPACE in it
  D:\\zo\\Zocomputer Agents\\_tools\\axis_hist.py.
- log:
- resolution:

### FU-778 | An entry with a title but no code anchors at all
- date: 2026-07-30 - source: test - status: watch - priority: P3
- class: improvement
- detail: purely a policy note, names no files.
- log:
- resolution:
"""


@pytest.fixture()
def agents(tmp_path):
    """A fake agents dir with the two graphify artifacts and a ledger."""
    g = tmp_path / "graphify"
    g.mkdir()
    (g / "_fu_index.json").write_text(json.dumps({
        "FU-181": {"status": "open", "title": "gate not volume aware",
                   "anchors": ["score_validity.py", "weekly_rescore.py"]},
    }), encoding="utf-8")
    (g / "fu_anchor_drift_last.json").write_text(json.dumps({
        "generated": "2026-07-30T00:00:00+00:00",
        "graph_commit": "deadbeefcafe",
        "drift": {"FU-181": {"status": "open", "title": "t",
                             "unresolved": ["sprint_import.py",
                                            "fu_context.py"]}},
    }), encoding="utf-8")
    (tmp_path / "FOLLOWUPS.md").write_text(LEDGER_SAMPLE, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------- cannot evaluate
def test_unknown_fu_cannot_evaluate(agents, capsys):
    """An FU in neither store must exit 2 -- never 0 with an empty answer."""
    rc = fu_context.main(["--fu", "9999", "--dry-run",
                          "--agents-dir", str(agents)])
    assert rc == fu_context.EXIT_CANNOT_EVALUATE
    assert "CANNOT EVALUATE" in capsys.readouterr().err


def test_non_numeric_fu_cannot_evaluate(agents):
    rc = fu_context.main(["--fu", "abc", "--dry-run",
                          "--agents-dir", str(agents)])
    assert rc == fu_context.EXIT_CANNOT_EVALUATE


def test_fu_with_title_but_no_anchors_is_ok_not_error(agents):
    """Distinct from cannot-evaluate: FU-778 exists and legitimately has no
    anchors. That is a real, small answer -- exit 0."""
    rc = fu_context.main(["--fu", "778", "--dry-run",
                          "--agents-dir", str(agents)])
    assert rc == fu_context.EXIT_OK


# ------------------------------------------------------------ honest degradation
def test_bus_unreachable_still_exits_zero(agents, monkeypatch, capsys):
    """The whole point of the file. Every transport down -> smaller answer, exit 0."""
    def dead(*_a, **_k):
        raise ConnectionRefusedError("10061")
    monkeypatch.setattr(fu_context, "_query_direct", dead)
    monkeypatch.setattr(fu_context, "_query_via_zo_call", dead)
    rc = fu_context.main(["--fu", "181", "--agents-dir", str(agents)])
    out = capsys.readouterr().out
    assert rc == fu_context.EXIT_OK
    assert "unavailable" in out
    assert "score_validity.py" in out, "anchors must survive a dead bus"


def test_bus_neighbors_never_raises(monkeypatch):
    """Even a bus that explodes in an unexpected way must degrade -- on EVERY
    transport, not just the first."""
    def boom(*_a, **_k):
        raise RuntimeError("socket on fire")
    monkeypatch.setattr(fu_context, "_query_direct", boom)
    monkeypatch.setattr(fu_context, "_query_via_zo_call", boom)
    rows, why = fu_context.bus_neighbors(["a.py"])
    assert rows is None
    assert "direct" in why and "zo_call" in why


def test_zo_call_used_when_direct_refuses(monkeypatch):
    """:8772 is a LOOPBACK on ZoComputer -- from the tower the direct call can
    never succeed, so the bridge must be tried rather than reported as failure."""
    def refused(*_a, **_k):
        raise ConnectionRefusedError("10061")
    monkeypatch.setattr(fu_context, "_query_direct", refused)
    monkeypatch.setattr(fu_context, "_query_via_zo_call",
                        lambda _sql: {"rows": [{"node": "x"}], "count": 1})
    rows, why = fu_context.bus_neighbors(["a.py"])
    assert rows == {"rows": [{"node": "x"}], "count": 1}
    assert why == "ok via zo_call"


# ------------------------------------------------------- the SQL schema contract
def test_sql_uses_relation_not_rel():
    """THE regression that mattered. v1 shipped `e.rel`, passed 19 tests and every
    CI gate, and was wrong -- the column is `relation`. It survived because the
    bus was unreachable so the query never ran. A query never executed is not a
    query verified."""
    sql = fu_context.neighbor_sql(["fu_ledger.py"])
    assert "e.relation AS rel" in sql
    assert "e.rel " not in sql and "e.rel," not in sql


def test_sql_only_names_real_kl_columns():
    """Pin the whole schema contract, not just the one column that bit."""
    sql = fu_context.neighbor_sql(["a.py"])
    for tok in re.findall(r"\b[nme]\.(\w+)", sql):
        assert tok in (fu_context.KL_NODE_COLS | fu_context.KL_EDGE_COLS), tok


def test_sql_avoids_dollar_anchored_regex():
    """`regexp_extract(..., '[^/]+$')` returns HTTP 400 through the
    PowerShell -> zo_call -> shell -> python quoting chain. LIKE survives it."""
    sql = fu_context.neighbor_sql(["a.py"])
    assert "regexp_extract" not in sql
    assert "LIKE '%/a.py'" in sql


def test_sql_matches_both_nested_and_root_paths():
    sql = fu_context.neighbor_sql(["go.sh"])
    assert "LIKE '%/go.sh'" in sql
    assert "= 'go.sh'" in sql


def test_sql_escapes_quotes_and_bounds_limit():
    sql = fu_context.neighbor_sql(["we'ird.py"], limit=7)
    assert "we''ird.py" in sql
    assert sql.rstrip().endswith("LIMIT 7")


def test_sql_with_no_anchors_matches_nothing():
    """An empty anchor list must not degenerate into WHERE () or a full scan."""
    assert "1=0" in fu_context.neighbor_sql([])


def test_dry_run_does_not_touch_the_bus(agents, monkeypatch):
    """--dry-run must be genuinely offline, not merely fast."""
    def tripwire(*_a, **_k):
        raise AssertionError("--dry-run reached the bus")
    monkeypatch.setattr(fu_context, "bus_neighbors", tripwire)
    rc = fu_context.main(["--fu", "181", "--dry-run",
                          "--agents-dir", str(agents)])
    assert rc == fu_context.EXIT_OK


# ------------------------------------------------------- scope, not breakage
def test_out_of_scope_is_not_called_stale(agents, tmp_path):
    """`sprint_import.py` is tower-local (absent from the repo tree) so it is
    out_of_scope; `fu_context.py` IS in the repo, so an unresolved anchor for it
    is real graph staleness. Conflating the two is the false red this guards."""
    ctx = fu_context.build_context("181", [], agents,
                                   agents / "FOLLOWUPS.md", use_bus=False)
    u = ctx["unresolved"]
    assert "sprint_import.py" in u["out_of_scope"]
    assert "fu_context.py" in u["in_repo_unindexed"]


def test_classify_empty_is_empty(tmp_path):
    u = fu_context.classify_unresolved([], tmp_path)
    assert u == {"out_of_scope": [], "in_repo_unindexed": []}


# ---------------------------------------------------------------- anchor hygiene
def test_path_variants_collapse_to_one_anchor(agents):
    """The ledger writes the same file three ways; the subgraph query must not
    ask for it three times."""
    anchors, status, title = fu_context.anchors_from_ledger(
        agents / "FOLLOWUPS.md", "777")
    norms = [fu_context._norm(a) for a in anchors]
    assert len(norms) == len(set(norms)), f"duplicate anchors: {anchors}"
    assert "weekly_rescore.py" in norms
    assert "go.sh" in norms
    assert status == "open"
    assert title.startswith("A sample entry")


def test_path_with_a_space_does_not_shear(agents):
    """REGRESSION, found by running the tool rather than by reasoning about it.
    `D:\\zo\\Zocomputer Agents\\_tools\\axis_hist.py` used to yield the anchor
    `Agents\\_tools\\axis_hist.py` -- the tail after the space -- which is a
    plausible-looking lie. Basename-only matching removes the class."""
    anchors, _, _ = fu_context.anchors_from_ledger(agents / "FOLLOWUPS.md", "777")
    assert "axis_hist.py" in anchors
    assert not any("Agents" in a for a in anchors), anchors
    assert not any(("/" in a) or ("\\" in a) for a in anchors), anchors


def test_json_configs_are_anchors_too(agents):
    """A schema contract is as much an anchor as a module; the first version
    matched only py/sh/sql/ps1/toml/yaml and silently dropped every .json."""
    anchors, _, _ = fu_context.anchors_from_ledger(agents / "FOLLOWUPS.md", "777")
    assert "risk_axis_mapping_v1.json" in anchors


def test_prose_is_not_mistaken_for_anchors(agents):
    """Only file-looking tokens become anchors -- otherwise every entry yields a
    fake subgraph."""
    anchors, _, _ = fu_context.anchors_from_ledger(agents / "FOLLOWUPS.md", "778")
    assert anchors == []


def test_extra_anchors_union_without_duplicating(agents):
    ctx = fu_context.build_context(
        "181", ["D:\\zo\\_runbook\\tools\\rescore\\score_validity.py", "new_one.py"],
        agents, agents / "FOLLOWUPS.md", use_bus=False)
    norms = [fu_context._norm(a) for a in ctx["anchors"]]
    assert len(norms) == len(set(norms))
    assert "new_one.py" in norms


def test_ledger_fallback_when_absent_from_index(agents):
    """FU-777 is only in the ledger -- a same-day entry is never in _fu_index."""
    ctx = fu_context.build_context("777", [], agents,
                                   agents / "FOLLOWUPS.md", use_bus=False)
    assert "FOLLOWUPS.md" in ctx["anchor_source"]
    assert ctx["anchors"], "ledger fallback produced no anchors"


def test_index_preferred_over_ledger(agents):
    ctx = fu_context.build_context("181", [], agents,
                                   agents / "FOLLOWUPS.md", use_bus=False)
    assert ctx["anchor_source"] == "_fu_index.json"
    assert ctx["graph_commit"] == "deadbeefcafe"


# ---------------------------------------------------------------- robustness
def test_missing_artifacts_do_not_crash(tmp_path):
    """No graphify dir at all: still must not traceback."""
    rc = fu_context.main(["--fu", "181", "--dry-run", "--agents-dir", str(tmp_path)])
    assert rc == fu_context.EXIT_CANNOT_EVALUATE


def test_corrupt_index_is_treated_as_absent(agents):
    (agents / "graphify" / "_fu_index.json").write_text("{not json",
                                                        encoding="utf-8")
    ctx = fu_context.build_context("777", [], agents,
                                   agents / "FOLLOWUPS.md", use_bus=False)
    assert "FOLLOWUPS.md" in ctx["anchor_source"]


def test_json_output_is_valid_json(agents, capsys):
    rc = fu_context.main(["--fu", "181", "--dry-run", "--json",
                          "--agents-dir", str(agents)])
    assert rc == fu_context.EXIT_OK
    json.loads(capsys.readouterr().out)


def test_direct_transport_posts_the_sql(monkeypatch):
    """The direct path must actually send the generated SQL as a JSON body."""
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"rows": [], "count": 0}'

    def fake_urlopen(req, timeout=None):
        seen["body"] = req.data.decode("utf-8")
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    rows, why = fu_context.bus_neighbors(["we'ird.py"])
    assert why == "ok via direct"
    assert "we''ird.py" in seen["body"]
    assert "e.relation AS rel" in seen["body"]


def test_render_survives_a_legacy_codepage_stdout():
    """A Unicode ledger title must not kill the render on a cp1252 stdout.

    This is the tower's seat expressed as a portable assertion. FU-103's real
    title carries U+21C4; the tower renders to cp1252; so the one command the
    PROTEAN CHARTER tells every lane to run exited rc=1 after doing all its
    work. The bug is invisible on Linux CI, so the test pins an explicit cp1252
    stream instead of trusting the ambient one -- that is what lets this
    assertion go RED where CI actually runs.
    """
    ctx = {
        "fu": "FU-103",
        "title": "Tie the ledger \u21c4 MCP memory \u21c4 graphify KL",
        "status": "in-progress",
        "anchor_source": "_fu_index.json",
        "anchors": ["tools/fu/fu_context.py"],
        "graph_commit": "deadbeefcafe",
        "kl": "unavailable",
        "subgraph": [],
        "unresolved": {"in_repo_unindexed": [], "out_of_scope": []},
    }
    text = fu_context.render(ctx)
    assert "\u21c4" in text

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    old = sys.stdout
    sys.stdout = stream
    try:
        fu_context._emit(text)   # must not raise
        stream.flush()
    finally:
        sys.stdout = old

    written = stream.buffer.getvalue().decode("cp1252")
    assert "FU-103" in written
    assert "graphify KL" in written


def test_negative_control_bare_print_would_have_failed():
    """Proves the guard above is not vacuous.

    If a bare print() to the same cp1252 stream did NOT raise, the previous test
    could pass against a build that never had the fix -- an assertion never seen
    RED is not evidence (HARNESS_DOCTRINE R4).
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    old = sys.stdout
    sys.stdout = stream
    try:
        with pytest.raises(UnicodeEncodeError):
            print("ledger \u21c4 KL")
    finally:
        sys.stdout = old


# --------------------------------------------------------------- bridge escapes
# zo_call changes its output REPRESENTATION above ~32 KiB. Both shapes are
# replayed here as literals so the recovery is tested without a bus, which is
# the only way this can go red on Linux CI.

_SMALL_FORM = (
    "CmdResult(stdout='{\"rows\":[{\"node\":\"a\",\"file\":\"x.py\"}],"
    "\"count\":1}\n', stderr='', returncode=0)\n"
)
_LARGE_FORM = (
    '[\n"{\\"rows\\":[{\\"node\\":\\"a\\",\\"file\\":\\"x.py\\"}],'
    '\\"count\\":174}\\n"\n]\n'
)


def test_recovers_the_small_zo_call_form():
    d = fu_context._recover_bus_json(_SMALL_FORM)
    assert d is not None and d["count"] == 1


def test_recovers_the_large_escaped_zo_call_form():
    """The 32 KiB cliff: a complete result must not be read as an absent bus.

    Measured 2026-07-30: FU-110's 34 anchors returned count=174 in 33,440B and
    the tool reported `kl: unavailable`. The payload was never the problem.
    """
    d = fu_context._recover_bus_json(_LARGE_FORM)
    assert d is not None, "escaped (>32KiB) form not recovered"
    assert d["count"] == 174


def test_negative_control_old_single_shape_regex_missed_the_large_form():
    """Proves the two tests above are not vacuous.

    The pre-fix recovery was one regex over the RAW stdout. It matched the small
    form and missed the large one -- so this asserts the old behaviour really
    was red on the large form, which is what makes the fix evidence rather than
    decoration (HARNESS_DOCTRINE R4).
    """
    old = re.compile(r'\{"rows".*?\}\]?,\s*"count":\s*\d+\}')
    assert old.search(_SMALL_FORM) is not None
    assert old.search(_LARGE_FORM) is None


def test_unrecoverable_payload_returns_none_not_an_exception():
    assert fu_context._recover_bus_json("total garbage, no json here") is None


# ------------------------------------------------------- adaptive limit ladder

def test_limit_ladder_descends_and_is_honoured_by_the_sql():
    assert list(fu_context.BUS_LIMIT_LADDER) == sorted(
        fu_context.BUS_LIMIT_LADDER, reverse=True)
    for lim in fu_context.BUS_LIMIT_LADDER:
        assert f"LIMIT {lim}" in fu_context.neighbor_sql(["a.py"], limit=lim)


def test_wide_fanout_steps_down_instead_of_reporting_unavailable(monkeypatch):
    """The FU-110 case: truncated at 400, intact at 150.

    Before this, a reachable bus plus a wide fan-out produced
    `kl: unavailable`, which is the same string an actually-unreachable bus
    produces -- so the two were indistinguishable to every reader.
    """
    seen = []

    def fake_direct(sql):
        raise OSError("loopback refuses the tower")

    def fake_zo_call(sql):
        seen.append(sql)
        if "LIMIT 400" in sql:
            raise RuntimeError("payload not recovered from 33440B of zo_call stdout")
        return {"rows": [{"node": "a"}], "count": 1}

    monkeypatch.setattr(fu_context, "_query_direct", fake_direct)
    monkeypatch.setattr(fu_context, "_query_via_zo_call", fake_zo_call)

    rows, reason = fu_context.bus_neighbors(["a.py"])
    assert rows is not None, f"stepped down but still gave up: {reason}"
    assert "ok via zo_call" in reason
    assert "capped at 150" in reason, reason
    assert any("LIMIT 400" in s for s in seen), "never tried the full budget first"


def test_reason_carries_the_message_not_just_the_exception_class(monkeypatch):
    """Negative control for finding #2.

    The pre-fix reason was f"{name}: {type(exc).__name__}" -- so this asserts the
    message survives. Without it the string is 'zo_call: RuntimeError', which is
    what hid a 32 KiB transport cliff behind an apparently-dead bus.
    """
    def boom(sql):
        raise RuntimeError("SPECIFIC-CAUSE-12345")

    monkeypatch.setattr(fu_context, "_query_direct", boom)
    monkeypatch.setattr(fu_context, "_query_via_zo_call", boom)

    rows, reason = fu_context.bus_neighbors(["a.py"])
    assert rows is None
    assert "SPECIFIC-CAUSE-12345" in reason, reason
    assert "RuntimeError" in reason


def test_no_anchors_is_not_an_error():
    rows, reason = fu_context.bus_neighbors([])
    assert rows is None and "no anchors" in reason


def test_kl_reports_the_basis_including_a_transport_cap(monkeypatch):
    """A capped subgraph must not render identically to a complete one (R5).

    Negative control: the pre-fix line was a hardcoded ctx["kl"] = "ok", so this
    assertion was RED before the change.
    """
    monkeypatch.setattr(fu_context, "bus_neighbors",
                        lambda anchors: ({"rows": [{"node": "a"}], "count": 1},
                                         "ok via zo_call (transport-capped at 150 edges)"))
    monkeypatch.setattr(fu_context, "load_index", lambda d: {
        "FU-777": {"status": "open", "title": "t", "anchors": ["a.py"]}})

    ctx = fu_context.build_context("777", [], fu_context.DEFAULT_AGENTS_DIR,
                                   fu_context.DEFAULT_AGENTS_DIR / "FOLLOWUPS.md",
                                   use_bus=True)
    assert ctx["kl"] != "ok", "the basis was discarded"
    assert "capped at 150" in ctx["kl"], ctx["kl"]
    assert "capped at 150" in fu_context.render(ctx)
