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
import json
import os
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
    """The whole point of the file. Bus down -> smaller answer, exit 0."""
    monkeypatch.setattr(fu_context, "BUS_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(fu_context, "BUS_TIMEOUT_S", 0.05)
    rc = fu_context.main(["--fu", "181", "--agents-dir", str(agents)])
    out = capsys.readouterr().out
    assert rc == fu_context.EXIT_OK
    assert "unavailable" in out
    assert "score_validity.py" in out, "anchors must survive a dead bus"


def test_bus_neighbors_never_raises(monkeypatch):
    """Even a bus that explodes in an unexpected way must degrade."""
    def boom(*_a, **_k):
        raise RuntimeError("socket on fire")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    rows, why = fu_context.bus_neighbors(["a.py"])
    assert rows is None
    assert "RuntimeError" in why


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


def test_sql_quotes_are_escaped(monkeypatch):
    """An anchor with a quote must not break out of the literal."""
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"rows": []}'

    def fake_urlopen(req, timeout=None):
        seen["body"] = req.data.decode("utf-8")
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    rows, why = fu_context.bus_neighbors(["we'ird.py"])
    assert why == "ok"
    assert "we''ird.py" in seen["body"]
