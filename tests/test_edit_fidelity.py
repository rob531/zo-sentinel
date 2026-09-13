"""Real assertions on tools/edit_fidelity (the arXiv:2609.04061 edit-fidelity harness).

Hermetic: every pytest subprocess runs inside a tmp_path mini-repo, never this
repo. No network: the Anthropic repairer is exercised through an injected
transport. The zero-runtime-dependency rule (README: FU-118) is itself a test.
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.edit_fidelity import corrupt as C  # noqa: E402
from tools.edit_fidelity import metrics as M  # noqa: E402
from tools.edit_fidelity import repairers as R  # noqa: E402
from tools.edit_fidelity import run_eval as E  # noqa: E402
from tools.edit_fidelity import tasks as T  # noqa: E402

PKG_DIR = REPO_ROOT / "tools" / "edit_fidelity"

CALC = '''"""Tiny module for the harness tests -- ünïcödé so byte/char offsets differ."""
import os  # unused on purpose: the remainder must stay byte-identical

LIMIT = 10  # module level: never a candidate


def clamp(x, lo, hi):
    """Clamp x into [lo, hi]."""
    # a comment the sloppy repairer will lose
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def classify(n, strict):
    total = 0
    for i in range(n):
        if i % 2 == 0 and i > 0:
            total += i
    if not strict:
        total += 1
    return max(total, 3)


def untested(a, b):
    if a == b:
        return 1
    return 0
'''

TEST_CALC = '''from pkg.calc import clamp, classify


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
    assert clamp(0, 0, 10) == 0


def test_classify():
    assert classify(5, True) == 6
    assert classify(5, False) == 7
    assert classify(0, True) == 3
'''

TEST_NOTHING = '''import pkg.calc


def test_smoke():
    assert pkg.calc.LIMIT == 10
'''


def _mini_repo(tmp_path: pathlib.Path, with_calc_test: bool = True) -> pathlib.Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    T.write_text(tmp_path / "pkg" / "calc.py", CALC)
    (tmp_path / "tests").mkdir()
    if with_calc_test:
        T.write_text(tmp_path / "tests" / "test_calc.py", TEST_CALC)
    T.write_text(tmp_path / "tests" / "test_nothing.py", TEST_NOTHING)
    return tmp_path


# --------------------------------------------------------------------------- corrupt

def test_every_class_present_with_exact_inverse_and_byte_identical_remainder():
    cands, stats = C.enumerate_candidates(CALC, "pkg/calc.py", seed=1)
    assert stats["syntax_rejects"] == 0
    assert set(stats["by_class"]) == set(C.CORRUPTION_CLASS_NAMES), stats["by_class"]
    for c in cands:
        corrupted = c.apply(CALC)
        assert corrupted != CALC
        ast.parse(corrupted)
        assert C.untouched_remainder_identical(CALC, corrupted, c), c.id
        assert c.invert(corrupted) == CALC, c.id
        assert c.func, c.id  # every candidate names its enclosing function
        # json round-trip keeps the inverse exact
        c2 = C.Corruption.from_dict(json.loads(json.dumps(c.to_dict())))
        assert c2.invert(c2.apply(CALC)) == CALC


def test_candidates_are_seeded_and_deterministic():
    a, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=5)
    b, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=5)
    c, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=6)
    assert [x.id for x in a] == [x.id for x in b]
    assert [x.id for x in a] != [x.id for x in c]
    assert sorted(x.id for x in a) == sorted(x.id for x in c)


def test_module_level_and_string_and_fstring_nodes_are_not_candidates():
    src = ('X = 1 < 2\n'
           'def f(a):\n'
           '    s = "1 < 2 and not x"\n'
           '    return f"{a < 1}" + str(a)\n')
    cands, _ = C.enumerate_candidates(src, "m.py", seed=0)
    assert [c.id for c in cands] == ["boundary_shift@4:22"] or all(c.lineno >= 2 for c in cands)
    assert not any(c.lineno == 1 for c in cands)


def test_apply_refuses_a_drifted_source():
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=1)
    with pytest.raises(ValueError):
        cands[0].apply(CALC.replace("LIMIT = 10", "LIMIT = 100"))


# --------------------------------------------------------------------------- metrics

def _naive_lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def test_levenshtein_known_values_and_random_agreement_with_naive():
    assert M.levenshtein("kitten", "sitting") == 3
    assert M.levenshtein("flaw", "lawn") == 2
    assert M.levenshtein("", "abc") == 3
    assert M.levenshtein("abc", "") == 3
    assert M.levenshtein("same", "same") == 0
    rng = random.Random(42)
    for _ in range(300):
        a = "".join(rng.choice("ab c\n") for _ in range(rng.randint(0, 14)))
        b = "".join(rng.choice("ab c\n") for _ in range(rng.randint(0, 14)))
        assert M.levenshtein(a, b) == _naive_lev(a, b), (a, b)


def test_fidelity_lev_is_line_ending_invariant():
    # measured 2026-09-12: a CRLF checkout vs LF model output scored 0.022 on a
    # byte-perfect repair -- exactly lines/chars. Line endings are not edits.
    crlf = CALC.replace("\n", "\r\n")
    assert M.fidelity_lev(crlf, CALC) == 0.0 and M.fidelity_lev(CALC, crlf) == 0.0
    assert M.fidelity_lev(crlf, CALC.replace("LIMIT = 10", "LIMIT = 11")) == pytest.approx(1 / len(CALC))
    assert M.levenshtein(crlf, CALC) == CALC.count("\n")  # the raw distance still sees them


def test_fidelity_lev_is_zero_only_for_identical_and_scales_by_reference_length():
    assert M.fidelity_lev(CALC, CALC) == 0.0
    assert M.fidelity_lev(CALC, CALC + "x") == pytest.approx(1 / len(CALC))
    with pytest.raises(ValueError):
        M.fidelity_lev("", "x")


@pytest.mark.parametrize("src,expected", [
    ("def f(x):\n    return x\n", 0),
    ("def f(x):\n    if x:\n        return 1\n    return 0\n", 1),
    ("def f(x):\n    if x:\n        return 1\n    elif x is None:\n        return 2\n    else:\n        return 3\n", 3),
    ("def f(xs):\n    for x in xs:\n        if x:\n            return x\n", 3),
    ("def f(a,b,c):\n    return a and b and c\n", 1),
    ("def f(a,b,c):\n    return a and b or c\n", 2),
    ("def f():\n    try:\n        g()\n    except ValueError:\n        if x:\n            pass\n", 3),
    ("def f():\n    with a:\n        if x:\n            pass\n", 3),
    ("class C:\n    def f(self, x):\n        if x:\n            pass\n", 1),
    ("def f():\n    def g():\n        if x:\n            pass\n    return g\n", 2),
    ("def f(x):\n    return 1 if x else 2\n", 1),
    ("if True:\n    pass\n", 1),
])
def test_cognitive_complexity_documented_rule_set(src, expected):
    assert M.cognitive_complexity(src) == expected


def test_delta_cc_sign():
    plain = "def f(x):\n    return x\n"
    guarded = "def f(x):\n    if x:\n        return x\n    return x\n"
    assert M.delta_cc(plain, guarded) == 1
    assert M.delta_cc(guarded, plain) == -1
    assert M.delta_cc(plain, plain) == 0


def test_pass_at_1_runs_a_subprocess_and_zero_collected_is_not_a_pass(tmp_path):
    repo = _mini_repo(tmp_path)
    ok = M.pass_at_1(repo, "tests/test_calc.py")
    assert ok["passed"] and ok["rc"] == 0 and ok["n_passed"] == 2
    T.write_text(repo / "pkg" / "calc.py", CALC.replace("return max(total, 3)", "return max(total, 4)"))
    M.purge_pyc(repo / "pkg" / "calc.py")
    bad = M.pass_at_1(repo, "tests/test_calc.py", touched=[repo / "pkg" / "calc.py"])
    assert not bad["passed"] and bad["rc"] == 1 and "FAILED" in bad["tail"]
    T.write_text(repo / "tests" / "test_empty.py", "x = 1\n")
    none = M.pass_at_1(repo, "tests/test_empty.py")
    assert not none["passed"] and none["collected_zero"]


# --------------------------------------------------------------------------- tasks / the gate

def test_sources_for_test_maps_imports_and_size_window(tmp_path):
    repo = _mini_repo(tmp_path)
    assert T.sources_for_test(repo, "tests/test_calc.py", min_lines=5, max_bytes=10_000) == ["pkg/calc.py"]
    assert T.sources_for_test(repo, "tests/test_calc.py", min_lines=5, max_bytes=100) == []  # too big
    assert T.sources_for_test(repo, "tests/test_calc.py", min_lines=500, max_bytes=10_000) == []  # too small


def test_validity_gate_keeps_red_discards_green_counts_and_restores(tmp_path):
    repo = _mini_repo(tmp_path)
    pairs = [("pkg/calc.py", "tests/test_nothing.py"), ("pkg/calc.py", "tests/test_calc.py")]
    tasks, gs = T.select_tasks(repo, pairs, n_target=3, seed=3, max_per_file=3,
                               max_candidates_per_file=6, log=lambda *_: None)
    assert gs.valid == len(tasks) == 3
    assert gs.discarded_green > 0, "the target that cannot see the module must produce discards"
    assert gs.candidates_tried == gs.valid + gs.discarded_green + gs.discarded_timeout
    assert len(gs.discarded_green_ids) == gs.discarded_green
    assert gs.syntax_rejects == 0
    assert T.read_text(repo / "pkg" / "calc.py") == CALC, "gate must restore the file byte-for-byte"
    for t in tasks:
        assert t.corrupted != t.reference
        assert t.corruption.invert(t.corrupted) == t.reference
        assert "FAILED" in t.fail_tail or "Error" in t.fail_tail
    # persisted task set round-trips with the gate counts attached
    p = tmp_path / "tasks.json"
    T.save_tasks(p, tasks, gs, {"seed": 3})
    tasks2, gate2, meta2 = T.load_tasks(p)
    assert [t.id for t in tasks2] == [t.id for t in tasks]
    assert gate2["discarded_green"] == gs.discarded_green and meta2["seed"] == 3


def test_inflight_marker_recovers_a_killed_swap(tmp_path):
    repo = _mini_repo(tmp_path)
    src = repo / "pkg" / "calc.py"
    sw = T.swapped_file(repo, "pkg/calc.py", CALC, CALC.replace("LIMIT = 10", "LIMIT = 11"))
    sw.__enter__()                       # simulate a kill: never __exit__
    assert (repo / T.INFLIGHT).is_file() and T.read_text(src) != CALC
    assert T.recover_inflight(repo) == "pkg/calc.py"
    assert T.read_text(src) == CALC and not (repo / T.INFLIGHT).exists()
    assert T.recover_inflight(repo) is None
    with T.swapped_file(repo, "pkg/calc.py", CALC, "x = 1\n"):
        assert T.read_text(src) == "x = 1\n" and (repo / T.INFLIGHT).is_file()
    assert T.read_text(src) == CALC and not (repo / T.INFLIGHT).exists()


def test_gate_treats_a_zero_collected_clean_run_as_not_green(tmp_path):
    repo = _mini_repo(tmp_path)
    T.write_text(repo / "tests" / "test_empty.py", "import pkg.calc\n")
    tasks, gs = T.select_tasks(repo, [("pkg/calc.py", "tests/test_empty.py")], n_target=1, seed=0,
                               log=lambda *_: None)
    assert tasks == [] and gs.pairs_clean_fail == 1 and gs.candidates_tried == 0


# --------------------------------------------------------------------------- repairers / poles

def _valid_tasks(tmp_path, n=2):
    repo = _mini_repo(tmp_path)
    tasks, _ = T.select_tasks(repo, [("pkg/calc.py", "tests/test_calc.py")], n_target=n, seed=11,
                              max_per_file=n, log=lambda *_: None)
    assert len(tasks) == n
    return repo, tasks


def test_oracle_pole_is_green(tmp_path):
    repo, tasks = _valid_tasks(tmp_path)
    for t in tasks:
        row = E.score_repair(repo, t, R.OracleRepairer().repair(t).output, timeout=120, python=None)
        assert row["passed"] and row["fidelity_lev"] == 0.0 and row["delta_cc"] == 0


def test_sloppy_pole_is_red_but_correct(tmp_path):
    repo, tasks = _valid_tasks(tmp_path)
    for t in tasks:
        out = R.SloppyRepairer().repair(t).output
        row = E.score_repair(repo, t, out, timeout=120, python=None)
        assert row["passed"], row["diff"]
        assert row["fidelity_lev"] > 0.02
        assert row["delta_cc"] > 0
        # semantics preserved for the rewritten function, not just the tested inputs
        ns_ref, ns_out = {}, {}
        exec(compile(t.reference, "ref", "exec"), ns_ref)
        exec(compile(out, "out", "exec"), ns_out)
        fn = t.corruption.func
        for args in [(0, True), (5, False), (7, True), (3, 0, 10), (-2, 0, 10), (12, 0, 10)]:
            try:
                exp = ns_ref[fn](*args)
            except TypeError:
                continue
            assert ns_out[fn](*args) == exp
    assert T.read_text(repo / "pkg" / "calc.py") == CALC


def test_check_poles_requires_both_to_separate():
    good_o = [{"task": "a", "passed": True, "fidelity_lev": 0.0, "delta_cc": 0}]
    good_s = [{"task": "a", "passed": True, "fidelity_lev": 0.2, "delta_cc": 1}]
    assert E.check_poles(good_o, good_s)["separated"]
    assert not E.check_poles([{"task": "a", "passed": True, "fidelity_lev": 0.001, "delta_cc": 0}], good_s)["separated"]
    assert not E.check_poles(good_o, [{"task": "a", "passed": False, "fidelity_lev": 0.2, "delta_cc": 1}])["separated"]
    assert not E.check_poles(good_o, [{"task": "a", "passed": True, "fidelity_lev": 0.2, "delta_cc": 0}])["separated"]
    assert not E.check_poles([], [])["separated"]


def test_anthropic_prompts_differ_only_by_the_preservation_instruction(tmp_path):
    _, tasks = _valid_tasks(tmp_path, n=1)
    plain = R.build_prompt(tasks[0], preservation=False)
    pres = R.build_prompt(tasks[0], preservation=True)
    assert pres.replace(R.PRESERVATION_INSTRUCTION + "\n\n", "") == plain
    assert R.PRESERVATION_INSTRUCTION not in plain


def test_anthropic_repairer_with_fake_transport_accounts_usage_and_caps(tmp_path):
    _, tasks = _valid_tasks(tmp_path, n=1)
    t = tasks[0]
    calls = []

    def fake_post(url, headers, body):
        calls.append(json.loads(body))
        assert headers["x-api-key"] == "sk-test"
        resp = {"model": "claude-sonnet-4-5", "stop_reason": "end_turn",
                "usage": {"input_tokens": 1000, "output_tokens": 500},
                "content": [{"type": "text", "text": "Here you go:\n```python\n" + t.reference.rstrip("\n") + "\n```\n"}]}
        return 200, json.dumps(resp)

    shared = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    rep = R.AnthropicRepairer(preservation=True, model="claude-sonnet-4-5", api_key="sk-test", max_calls=2,
                              max_cost_usd=1.0, post=fake_post, shared_usage=shared)
    res = rep.repair(t)
    assert res.output == t.reference and res.meta["fenced"] and not res.meta["truncated"]
    assert calls[0]["model"] == "claude-sonnet-4-5" and calls[0]["max_tokens"] == 4096
    assert R.PRESERVATION_INSTRUCTION in calls[0]["messages"][0]["content"]
    u = rep.usage_summary()
    assert u["calls"] == 1 and u["input_tokens"] == 1000 and u["output_tokens"] == 500
    assert u["est_cost_usd"] == pytest.approx((1000 * 3 + 500 * 15) / 1e6) and u["pricing_known"]
    rep.repair(t)
    with pytest.raises(R.BudgetExceeded):
        rep.repair(t)  # call cap
    # a second arm sharing the usage dict is capped on COST across both arms
    rep2 = R.AnthropicRepairer(preservation=False, model="claude-sonnet-4-5", api_key="sk-test", max_calls=99,
                               max_cost_usd=0.01, post=fake_post, shared_usage=shared)
    with pytest.raises(R.BudgetExceeded):
        rep2.repair(t)


def test_extract_code_takes_the_largest_fence_not_the_first():
    # measured 2026-09-12: the model put a 47-byte diagnostic snippet in a first
    # fence and the whole file in a second; first-fence extraction scored fid 0.99
    text = "The bug:\n```python\nx == y\n```\nFixed file:\n```python\nimport os\n\n\ndef f():\n    return 1\n```\n"
    assert R.extract_code(text) == "import os\n\n\ndef f():\n    return 1\n"


def test_extract_code_and_cost_fallback():
    assert R.extract_code("x\n```python\nprint(1)\n```\ny") == "print(1)\n"
    assert R.extract_code("```\nprint(1)\n```") == "print(1)\n"
    assert R.extract_code("no fence") == "no fence"
    cost, known = R.estimate_cost_usd("some-unknown-model", 1_000_000, 0)
    assert cost == 3.0 and not known


# --------------------------------------------------------------------------- run_eval

def _run_eval(repo, *extra):
    cmd = [sys.executable, str(PKG_DIR / "run_eval.py"), "--repo", str(repo), "--out-dir", str(repo / "_out"),
           "--tasks-json", str(repo / "_out" / "tasks.json"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT))


def test_run_eval_unevaluable_exit_code_when_no_task_survives(tmp_path):
    repo = _mini_repo(tmp_path, with_calc_test=False)
    targets = tmp_path / "targets.txt"
    targets.write_text("tests/test_nothing.py\n", encoding="utf-8")
    p = _run_eval(repo, "--targets", str(targets), "--min-lines", "5", "--n-tasks", "2", "--max-candidates-per-file", "3")
    assert p.returncode == E.EXIT_UNEVALUABLE, p.stdout + p.stderr
    assert "UNEVALUABLE" in p.stdout
    results = list((repo / "_out").glob("results_*.json"))
    assert results and json.loads(results[0].read_text())["exit_code"] == E.EXIT_UNEVALUABLE


def test_run_eval_self_test_proves_both_poles_on_a_live_file(tmp_path):
    repo = _mini_repo(tmp_path)
    targets = tmp_path / "targets.txt"
    targets.write_text("tests/test_calc.py\n", encoding="utf-8")
    p = _run_eval(repo, "--self-test", "--self-test-n", "2", "--targets", str(targets), "--min-lines", "5")
    assert p.returncode == E.EXIT_OK, p.stdout + p.stderr
    assert "separated=True" in p.stdout
    report = list((repo / "_out").glob("report_*.md"))[0].read_text(encoding="utf-8")
    assert "oracle GREEN pole" in report and "**True**" in report
    assert T.read_text(repo / "pkg" / "calc.py") == CALC


def test_model_arms_are_interleaved_and_a_budget_trip_keeps_paired_rows(tmp_path):
    repo, tasks = _valid_tasks(tmp_path, n=2)

    def fake_post(url, headers, body):
        t = next(x for x in tasks if x.corrupted in json.loads(body)["messages"][0]["content"])
        return 200, json.dumps({"model": "m", "stop_reason": "end_turn",
                                "usage": {"input_tokens": 10, "output_tokens": 10},
                                "content": [{"type": "text", "text": "```python\n" + t.reference + "```"}]})

    shared = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    reps = {"anthropic_plain": R.AnthropicRepairer(False, "claude-sonnet-4-5", "k", post=fake_post, shared_usage=shared,
                                                   max_calls=2, max_cost_usd=9),
            "anthropic_preserve": R.AnthropicRepairer(True, "claude-sonnet-4-5", "k", post=fake_post, shared_usage=shared,
                                                      max_calls=2, max_cost_usd=9)}
    rows = E.run_arms_interleaved(repo, reps, tasks, timeout=120, python=None)
    assert [r["task"] for r in rows["anthropic_plain"]] == [r["task"] for r in rows["anthropic_preserve"]]
    assert all(r["passed"] and r["fidelity_lev"] == 0.0 and "output" in r for a in rows for r in rows[a])
    # cap at 3 total calls: task 1 both arms, task 2 plain, then the cap trips -> rows kept, still paired on task 1
    reps["anthropic_plain"].max_calls = 2
    reps["anthropic_preserve"].max_calls = 1
    for r in reps.values():
        r.calls_here = 0
    with pytest.raises(R.BudgetExceeded) as ei:
        E.run_arms_interleaved(repo, reps, tasks, timeout=120, python=None)
    kept = ei.value.rows
    assert len(kept["anthropic_plain"]) == 2 and len(kept["anthropic_preserve"]) == 1


def test_sign_test_is_two_sided_and_drops_ties():
    r = E.sign_test([0.0] * 5, [0.1] * 5)
    assert r["wins"] == 5 and r["losses"] == 0 and r["p_two_sided"] == pytest.approx(2 / 32)
    assert E.sign_test([1.0, 2.0], [1.0, 2.0])["p_two_sided"] is None


# --------------------------------------------------------------------------- the zero-dependency rule

def test_harness_imports_only_stdlib():
    stdlib = set(sys.stdlib_module_names)
    for py in PKG_DIR.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: the package itself
                    continue
                names = [node.module]
            else:
                continue
            for n in names:
                top = n.split(".")[0]
                assert top in stdlib or top == "tools", f"{py.name}: non-stdlib import {n!r} (README: FU-118)"
