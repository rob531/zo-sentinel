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
from tools.edit_fidelity import merge_runs as MR  # noqa: E402
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
        assert t.shape == T.SHAPE_EASY and t.n_nodes == 1
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
        exec(compile(t.reference, "ref", "exec"), ns_ref)  # nosec B102 - the test's own fixture module
        exec(compile(out, "out", "exec"), ns_out)  # nosec B102 - the sloppy rewrite of that fixture
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
    # the FULL response is kept so an extractor fix can re-score offline, without
    # buying the run again (measured 2026-09-13: that gap cost a $2.68 A/B)
    assert res.meta["raw_text"].startswith("Here you go:")
    assert t.reference.rstrip("\n") in res.meta["raw_text"]
    assert R.extract_code(res.meta["raw_text"]) == res.output
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


def test_extract_code_takes_the_module_not_the_prose_around_indented_fences():
    """Measured 2026-09-13: the shape of the answer that cost a $2.68 A/B.

    Under the hard prompt the model replies with a numbered list whose fences
    are INDENTED, then the whole module in a column-0 fence. The old pattern
    could not close an indented fence, so one match ran across the prose and
    `max(..., key=len)` preferred that blob to the module: 10 of 30 plain-arm
    rows were scored as unparseable ~0.9-fidelity failures while the correct
    module sat in the same response.
    """
    module = ('"""Mod."""\n\n\ndef f(a, b):\n    if isinstance(a, str):\n'
              '        return a\n    return b\n')
    text = (
        "I found two bugs.\n\n"
        "1. On line 5:\n"
        "   ```python\n"
        "   if isinstance(str, a):\n"
        "   ```\n"
        "   The arguments are the wrong way round.\n\n"
        "2. Same on line 9:\n"
        "   ```python\n"
        "   if not isinstance(dict, content):\n"
        "   ```\n"
        "   Should be `isinstance(content, dict)`.\n\n"
        "Here is the corrected module:\n\n"
        "```python\n" + module + "```\n"
    )
    out = R.extract_code(text)
    assert out.rstrip("\n") == module.rstrip("\n"), out
    ast.parse(out)
    # the prose between the indented fences must never be what comes back
    assert "wrong way round" not in out and "Should be" not in out
    # every fence is seen, including the indented ones
    assert len(R._FENCE_RE.findall(text)) == 3


def test_extract_code_prefers_a_parsable_block_but_still_returns_broken_code():
    # nothing parses -> the largest block is returned, so a broken repair is
    # scored as broken instead of being replaced by something that compiles
    only_broken = "```python\ndef f(:\n    pass\n```"
    assert "def f(:" in R.extract_code(only_broken)
    # a large prose blob loses to a smaller real module
    mixed = ("```\n" + "this is not python at all, " * 40 + "\n```\n"
             "```python\ndef g():\n    return 1\n```\n")
    assert R.extract_code(mixed) == "def g():\n    return 1\n"


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


def test_key_cmd_is_split_without_a_shell():
    if sys.platform == "win32":
        assert E._split_cmd(r'"C:\Program Files\py.exe" D:\v\fetch_secret.py anthropic') == \
            [r"C:\Program Files\py.exe", r"D:\v\fetch_secret.py", "anthropic"]
    else:
        assert E._split_cmd('"/opt/my py/python" /v/fetch_secret.py anthropic') == \
            ["/opt/my py/python", "/v/fetch_secret.py", "anthropic"]
    # the key never touches a shell: a metacharacter is just an argument
    assert E._split_cmd("echo hi;rm") == ["echo", "hi;rm"]


def test_sign_test_is_two_sided_and_drops_ties():
    r = E.sign_test([0.0] * 5, [0.1] * 5)
    assert r["wins"] == 5 and r["losses"] == 0 and r["p_two_sided"] == pytest.approx(2 / 32)
    assert E.sign_test([1.0, 2.0], [1.0, 2.0])["p_two_sided"] is None


# --------------------------------------------------------------------------- hard shape: multi-node splices

def _two_candidates_in_different_functions(seed=1):
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=seed)
    by_func = {}
    for c in cands:
        by_func.setdefault(c.func, []).append(c)
    funcs = sorted(by_func)
    assert len(funcs) >= 2, funcs
    return [by_func[funcs[0]][0], by_func[funcs[1]][0]]


def test_apply_all_and_invert_all_round_trip_exactly_and_keep_the_remainder():
    picks = _two_candidates_in_different_functions()
    corrupted = C.apply_all(CALC, picks)
    assert corrupted != CALC
    ast.parse(corrupted)
    assert C.invert_all(corrupted, picks) == CALC
    assert C.untouched_remainder_identical_multi(CALC, corrupted, picks)
    assert C.apply_all(CALC, list(reversed(picks))) == corrupted, "input order must not matter"
    # applying one node alone must not reproduce the two-node text
    for c in picks:
        assert c.apply(CALC) != corrupted


def test_apply_all_refuses_overlapping_splices():
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=1)
    c = cands[0]
    twin = C.Corruption(**{**c.to_dict(), "note": "same span, different object"})
    assert not C.spans_disjoint([c, twin])
    with pytest.raises(ValueError):
        C.apply_all(CALC, [c, twin])


def test_untouched_remainder_multi_catches_an_edit_outside_the_spans():
    picks = _two_candidates_in_different_functions()
    corrupted = C.apply_all(CALC, picks)
    tampered = corrupted.replace("LIMIT = 10", "LIMIT = 99")
    assert tampered != corrupted
    assert not C.untouched_remainder_identical_multi(CALC, tampered, picks)


def test_min_func_lines_filters_and_prefer_long_only_reorders():
    base, bstats = C.enumerate_candidates(CALC, "pkg/calc.py", seed=7)
    assert bstats["short_func_rejects"] == 0
    # `untested` is 4 lines; `clamp` and `classify` are 8
    long_only, lstats = C.enumerate_candidates(CALC, "pkg/calc.py", seed=7, min_func_lines=8)
    assert lstats["short_func_rejects"] > 0
    assert long_only and len(long_only) < len(base)
    assert all(c.func_lines >= 8 for c in long_only)
    assert not any(c.func == "untested" for c in long_only)
    ordered, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=7, prefer_long=True)
    lens = [c.func_lines for c in ordered]
    assert lens == sorted(lens, reverse=True)
    assert sorted(c.id for c in ordered) == sorted(c.id for c in base), "a reordering, not a filter"
    again, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=7, prefer_long=True)
    assert [c.id for c in again] == [c.id for c in ordered], "still deterministic from the seed"


def test_build_combos_prefers_distinct_functions_and_never_reuses_a_component():
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=2)
    assert len({c.func for c in cands}) >= 2, "the fixture must offer several functions"
    combos = T.build_combos(cands, (2,), random.Random(0), limit=3)
    assert combos
    seen = []
    for combo in combos:
        assert len(combo) == 2
        assert C.spans_disjoint(combo)
        assert [c.start for c in combo] == sorted(c.start for c in combo)
        seen.extend(id(c) for c in combo)
    assert len(seen) == len(set(seen)), "a component may belong to at most one combo"
    assert len({c.func for c in combos[0]}) == 2, "with functions to spare, the first combo spreads"


def test_build_combos_stops_when_the_pool_cannot_fill_another_set():
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=2)
    assert T.build_combos(cands[:1], (2,), random.Random(0), limit=5) == []
    assert len(T.build_combos(cands, (2,), random.Random(0), limit=99)) <= len(cands) // 2
    assert len(T.build_combos(cands, (3,), random.Random(0), limit=1)[0]) == 3


# --------------------------------------------------------------------------- hard shape: the gate

def _hard_tasks(tmp_path, n=2, nodes=(2,)):
    repo = _mini_repo(tmp_path)
    tasks, gs = T.select_tasks(repo, [("pkg/calc.py", "tests/test_calc.py")], n_target=n, seed=11,
                               max_per_file=n, max_candidates_per_file=14, shape=T.SHAPE_HARD,
                               nodes=nodes, prefer_long=True, log=lambda *_: None)
    assert len(tasks) == n, f"fixture produced {len(tasks)} hard tasks, wanted {n}"
    return repo, tasks, gs


def test_hard_gate_builds_multi_node_tasks_whose_components_are_each_red(tmp_path):
    repo, tasks, gs = _hard_tasks(tmp_path, n=2)
    assert gs.valid == 2
    assert gs.components_valid >= 4, "each node of each task had to be red on its own"
    assert gs.combos_tried >= gs.valid
    assert gs.combos_green == gs.combos_tried - gs.valid
    assert gs.candidates_tried == gs.components_valid + gs.discarded_green + gs.discarded_timeout
    assert gs.func_len_mean > 0 and gs.nodes_hist == {"2": 2}
    assert T.read_text(repo / "pkg" / "calc.py") == CALC, "the gate must restore the file byte-for-byte"
    for t in tasks:
        assert t.shape == T.SHAPE_HARD and t.n_nodes == 2
        assert C.spans_disjoint(t.corruptions)
        assert t.corrupted == C.apply_all(t.reference, t.corruptions)
        assert C.invert_all(t.corrupted, t.corruptions) == t.reference
        assert t.id.count("+") == 1 and t.corruption is t.corruptions[0]
        for c in t.corruptions:
            fails, _ = T.gate_one(repo, t.source_file, t.test_target, c, t.reference, timeout=120)
            assert fails, f"component {c.id} is not red on its own"
    assert T.read_text(repo / "pkg" / "calc.py") == CALC


def test_hard_task_round_trips_through_json_with_every_node(tmp_path):
    _, tasks, gs = _hard_tasks(tmp_path, n=1)
    out = tmp_path / "hard.json"
    T.save_tasks(out, tasks, gs, {"seed": 11, "shape": T.SHAPE_HARD})
    again, gate2, meta2 = T.load_tasks(out)
    assert [t.id for t in again] == [t.id for t in tasks]
    assert again[0].shape == T.SHAPE_HARD and again[0].n_nodes == tasks[0].n_nodes
    assert C.invert_all(again[0].corrupted, again[0].corruptions) == again[0].reference
    assert gate2["components_valid"] == gs.components_valid and meta2["shape"] == T.SHAPE_HARD


def test_task_from_dict_still_reads_a_single_node_record():
    """The easy shape's on-disk form predates `corruptions`; it must still load."""
    cands, _ = C.enumerate_candidates(CALC, "pkg/calc.py", seed=1)
    c = cands[0]
    legacy = {"source_file": "pkg/calc.py", "test_target": "tests/test_calc.py",
              "corruption": c.to_dict(), "reference": CALC, "corrupted": c.apply(CALC)}
    t = T.Task.from_dict(legacy)
    assert t.n_nodes == 1 and t.shape == T.SHAPE_EASY and t.corruption.id == c.id
    assert t.funcs == [c.func]


def test_select_tasks_rejects_an_unknown_shape(tmp_path):
    repo = _mini_repo(tmp_path)
    with pytest.raises(ValueError):
        T.select_tasks(repo, [("pkg/calc.py", "tests/test_calc.py")], n_target=1, shape="medium",
                       log=lambda *_: None)


# --------------------------------------------------------------------------- hard shape: repairers

def test_oracle_undoes_every_node_of_a_hard_task(tmp_path):
    repo, tasks, _ = _hard_tasks(tmp_path, n=2)
    for t in tasks:
        res = R.OracleRepairer().repair(t)
        assert res.meta["inverse_of"] == [c.id for c in t.corruptions]
        row = E.score_repair(repo, t, res.output, timeout=120, python=None)
        assert row["passed"] and row["fidelity_lev"] == 0.0 and row["delta_cc"] == 0


def test_sloppy_rewrites_every_function_a_hard_task_touches(tmp_path):
    repo, tasks, _ = _hard_tasks(tmp_path, n=2)
    for t in tasks:
        res = R.SloppyRepairer().repair(t)
        row = E.score_repair(repo, t, res.output, timeout=120, python=None)
        assert row["passed"], row["diff"]
        assert row["fidelity_lev"] > 0.02 and row["delta_cc"] > 0
        assert res.meta["rewrote"] == t.funcs
        for fn in t.funcs:
            assert f"def {fn.split('.')[-1]}(" in res.output, f"{fn} vanished from the rewrite"
        # semantics of every rewritten function survive, not just the tested inputs
        ns_ref, ns_out = {}, {}
        exec(compile(t.reference, "ref", "exec"), ns_ref)  # nosec B102 - the test's own fixture module
        exec(compile(res.output, "out", "exec"), ns_out)  # nosec B102 - the sloppy rewrite of that fixture
        for fn in t.funcs:
            for args in [(0, True), (5, False), (7, True), (3, 0, 10), (-2, 0, 10), (12, 0, 10)]:
                try:
                    exp = ns_ref[fn](*args)
                except TypeError:
                    continue
                assert ns_out[fn](*args) == exp
    assert T.read_text(repo / "pkg" / "calc.py") == CALC


def test_hard_prompt_withholds_function_test_and_tail_but_differs_only_by_preservation(tmp_path):
    _, hard, _ = _hard_tasks(tmp_path, n=1)
    t = hard[0]
    plain = R.build_prompt(t, preservation=False)
    pres = R.build_prompt(t, preservation=True)
    assert pres.replace(R.PRESERVATION_INSTRUCTION + "\n\n", "") == plain
    assert R.HARD_TASK_STATEMENT in plain
    assert t.test_target not in plain, "hard prompt named the failing test target"
    assert "pytest output" not in plain and t.fail_tail not in plain, "hard prompt leaked the tail"
    for c in t.corruptions:
        assert f"`{c.func}`" not in plain, "hard prompt named the enclosing function"
    assert t.corrupted in plain, "the file itself is still handed over"
    # the only string separating the arms must carry no task information
    assert not any(ch.isdigit() for ch in R.PRESERVATION_INSTRUCTION)
    for word in ("two", "three", "both", "each bug", "function `"):
        assert word not in R.PRESERVATION_INSTRUCTION.lower()


def test_easy_prompt_is_unchanged_by_the_hard_shape_support(tmp_path):
    _, easy = _valid_tasks(tmp_path, n=1)
    plain = R.build_prompt(easy[0], preservation=False)
    assert easy[0].test_target in plain and "pytest output" in plain
    assert f"`{easy[0].corruption.func}`" in plain
    assert R.HARD_TASK_STATEMENT not in plain


# --------------------------------------------------------------------------- hard shape: analysis

def test_split_by_pass_separates_broken_repairs_from_over_edits():
    rows = [{"passed": True, "fidelity_lev": 0.0, "delta_cc": 0},
            {"passed": True, "fidelity_lev": 0.02, "delta_cc": 1},
            {"passed": False, "fidelity_lev": 0.90, "delta_cc": -3}]
    sp = E.split_by_pass(rows)
    assert sp["passed"]["n"] == 2 and sp["failed"]["n"] == 1
    assert sp["passed"]["fidelity_lev_mean"] == pytest.approx(0.01)
    assert sp["failed"]["fidelity_lev_mean"] == pytest.approx(0.90)
    assert sp["passed"]["at_floor"] == 1
    # a broken repair must not be able to inflate the passed (over-editing) mean
    assert sp["passed"]["fidelity_lev_mean"] < sp["failed"]["fidelity_lev_mean"]
    assert E.aggregate(rows)["fidelity_lev_mean"] > sp["passed"]["fidelity_lev_mean"]


def test_sign_test_power_is_monotone_and_honest_about_small_n():
    assert E.sign_test_power(0, 0.9) == 0.0
    assert E.sign_test_power(5, 0.9) == 0.0, "n=5 cannot reject at alpha=0.05 two-sided"
    assert E.sign_test_power(30, 0.5) == pytest.approx(0.05, abs=0.03)   # ~alpha under the null
    assert E.sign_test_power(30, 0.85) > E.sign_test_power(30, 0.75) > E.sign_test_power(30, 0.65)
    assert E.sign_test_power(60, 0.75) > E.sign_test_power(20, 0.75)
    assert 0.0 <= E.sign_test_power(13, 0.75) <= 1.0


def test_min_detectable_preference_matches_the_power_curve():
    n = 13
    p = E.min_detectable_preference(n)
    assert p is not None and E.sign_test_power(n, p) >= 0.8
    assert E.sign_test_power(n, round(p - 0.01, 2)) < 0.8
    assert E.min_detectable_preference(200) < E.min_detectable_preference(20)
    assert E.min_detectable_preference(4) is None, "no sample this small can ever reject"


def test_resolve_shape_defaults_by_shape_and_an_explicit_flag_always_wins():
    ap = E.build_parser()
    easy = E.resolve_shape(ap.parse_args([]))
    assert easy == {"shape": "easy", "nodes": (1,), "min_lines": T.DEFAULT_MIN_LINES,
                    "max_bytes": T.DEFAULT_MAX_BYTES, "min_func_lines": 0, "prefer_long": False}
    hard = E.resolve_shape(ap.parse_args(["--shape", "hard"]))
    assert hard["nodes"] == T.HARD_NODES and hard["prefer_long"]
    assert hard["min_lines"] == T.HARD_MIN_LINES and hard["max_bytes"] == T.HARD_MAX_BYTES
    assert hard["min_func_lines"] == T.HARD_MIN_FUNC_LINES
    override = E.resolve_shape(ap.parse_args(["--shape", "hard", "--min-lines", "5",
                                              "--min-func-lines", "0", "--nodes", "2"]))
    assert override["min_lines"] == 5 and override["min_func_lines"] == 0 and override["nodes"] == (2,)
    with pytest.raises(ValueError):
        E.resolve_shape(ap.parse_args(["--nodes", "2"]))              # easy shape takes no nodes
    with pytest.raises(ValueError):
        E.resolve_shape(ap.parse_args(["--shape", "hard", "--nodes", "0"]))


def test_run_eval_self_test_separates_the_poles_under_the_hard_shape(tmp_path):
    repo = _mini_repo(tmp_path)
    targets = tmp_path / "targets.txt"
    targets.write_text("tests/test_calc.py\n", encoding="utf-8")
    p = _run_eval(repo, "--shape", "hard", "--self-test", "--self-test-n", "2", "--targets", str(targets),
                  "--min-lines", "5", "--min-func-lines", "0", "--nodes", "2",
                  "--max-candidates-per-file", "14")
    assert p.returncode == E.EXIT_OK, p.stdout + p.stderr
    assert "separated=True" in p.stdout
    report = list((repo / "_out").glob("report_*.md"))[0].read_text(encoding="utf-8")
    assert "task shape **hard**" in report and "individually-red components" in report
    assert "oracle GREEN pole" in report and "**True**" in report
    assert "split by pass@1 outcome" in report
    results = json.loads(list((repo / "_out").glob("results_*.json"))[0].read_text(encoding="utf-8"))
    assert results["shape"]["shape"] == "hard" and results["shape"]["nodes"] == [2]
    assert results["task_ids"] and all(t.count("+") == 1 for t in results["task_ids"])
    assert T.read_text(repo / "pkg" / "calc.py") == CALC


def test_only_tasks_runs_a_subset_and_a_bad_id_is_unevaluable_not_empty(tmp_path):
    repo = _mini_repo(tmp_path)
    targets = tmp_path / "targets.txt"
    targets.write_text("tests/test_calc.py\n", encoding="utf-8")
    common = ["--targets", str(targets), "--min-lines", "5", "--max-candidates-per-file", "8"]
    first = _run_eval(repo, "--n-tasks", "2", *common)
    assert first.returncode == E.EXIT_OK, first.stdout + first.stderr
    ids = json.loads((repo / "_out" / "tasks.json").read_text(encoding="utf-8"))
    ids = [t["id"] for t in ids["tasks"]]
    assert len(ids) == 2
    only = _run_eval(repo, "--n-tasks", "2", "--only-tasks", ids[1], *common)
    assert only.returncode == E.EXIT_OK, only.stdout + only.stderr
    assert "--only-tasks: running 1 of 2" in only.stdout
    res = sorted((repo / "_out").glob("results_*.json"))[-1]
    payload = json.loads(res.read_text(encoding="utf-8"))
    assert payload["task_ids"] == [ids[1]]
    assert all(len(rows) == 1 for rows in payload["rows"].values())
    # a typo must never read as a clean run over nothing
    bad = _run_eval(repo, "--n-tasks", "2", "--only-tasks", ids[0] + ",nope::x@1:1", *common)
    assert bad.returncode == E.EXIT_UNEVALUABLE, bad.stdout + bad.stderr
    assert "UNEVALUABLE" in bad.stdout


def test_run_eval_refuses_to_mix_a_cached_shape_with_another(tmp_path):
    repo = _mini_repo(tmp_path)
    targets = tmp_path / "targets.txt"
    targets.write_text("tests/test_calc.py\n", encoding="utf-8")
    common = ["--targets", str(targets), "--min-lines", "5", "--min-func-lines", "0",
              "--max-candidates-per-file", "14"]
    first = _run_eval(repo, "--shape", "hard", "--nodes", "2", "--n-tasks", "1", *common)
    assert first.returncode == E.EXIT_OK, first.stdout + first.stderr
    second = _run_eval(repo, "--n-tasks", "1", *common)   # easy shape, same --tasks-json
    assert second.returncode == E.EXIT_ERROR, second.stdout + second.stderr
    assert "cached tasks are shape" in second.stdout


# --------------------------------------------------------------------------- merge_runs

def _payload(ts, rows, tasks_json="tj.json", shape="hard"):
    return {"ts": ts, "tasks_json": tasks_json, "shape": {"shape": shape, "nodes": [2]},
            "gate": {}, "task_ids": sorted({r["task"] for rs in rows.values() for r in rs}),
            "rows": rows, "aggregate": {}}


def _row(task, passed, fid, dcc=0, syntax=False):
    r = {"task": task, "passed": passed, "fidelity_lev": fid, "delta_cc": dcc, "n_passed": 1}
    if syntax:
        r["syntax_error"] = True
        r["delta_cc"] = None
    return r


def test_merge_replaces_only_named_rows_and_recomputes_every_aggregate():
    base = _payload("base", {
        "anthropic_plain": [_row("t1", False, 0.92, syntax=True), _row("t2", True, 0.0)],
        "anthropic_preserve": [_row("t1", True, 0.0), _row("t2", True, 0.0)],
    })
    ov = _payload("fixed", {"anthropic_plain": [_row("t1", True, 0.0)]})
    out = MR.merge(base, [ov])
    plain = {r["task"]: r for r in out["rows"]["anthropic_plain"]}
    assert plain["t1"]["passed"] and plain["t1"]["fidelity_lev"] == 0.0
    assert plain["t1"]["source_run"] == "fixed" and plain["t2"]["source_run"] == "base"
    assert out["merged_from"]["rows_replaced"] == {"anthropic_plain": 1}
    # aggregates are recomputed, not copied from the base
    assert out["aggregate"]["anthropic_plain"]["pass_at_1"] == 1.0
    assert out["aggregate"]["anthropic_plain"]["fidelity_lev_mean"] == 0.0
    assert out["split_by_pass"]["anthropic_plain"]["passed"]["at_floor"] == 2
    assert out["paired"]["n"] == 2 and out["paired"]["pass_plain"] == 2


def test_merge_reports_a_paired_mcnemar_on_pass_at_1():
    base = _payload("base", {
        "anthropic_plain": [_row("t1", True, 0.0), _row("t2", True, 0.0), _row("t3", False, 0.1)],
        "anthropic_preserve": [_row("t1", False, 0.2), _row("t2", True, 0.0), _row("t3", False, 0.1)],
    })
    out = MR.recompute(base)
    mc = out["paired"]["pass_mcnemar"]
    assert mc["plain_only"] == 1 and mc["preserve_only"] == 0 and mc["concordant"] == 2
    assert mc["p_two_sided"] == 1.0          # one discordant pair proves nothing
    assert out["paired"]["pass_plain"] == 2 and out["paired"]["pass_preserve"] == 1


def test_merge_refuses_to_invent_a_row_or_cross_task_sets():
    base = _payload("base", {"anthropic_plain": [_row("t1", True, 0.0)]})
    with pytest.raises(ValueError, match="not in the base run"):
        MR.merge(base, [_payload("ov", {"anthropic_plain": [_row("nope", True, 0.0)]})])
    with pytest.raises(ValueError, match="not in the base run"):
        MR.merge(base, [_payload("ov", {"sloppy": [_row("t1", True, 0.0)]})])
    with pytest.raises(ValueError, match="different --tasks-json"):
        MR.merge(base, [_payload("ov", {"anthropic_plain": [_row("t1", True, 0.0)]}, tasks_json="other.json")])
    with pytest.raises(ValueError, match="different task shape"):
        MR.merge(base, [_payload("ov", {"anthropic_plain": [_row("t1", True, 0.0)]}, shape="easy")])


def test_merge_runs_cli_writes_a_merged_report(tmp_path):
    base = _payload("base", {
        "anthropic_plain": [_row("t1", False, 0.92, syntax=True)],
        "anthropic_preserve": [_row("t1", True, 0.0)],
    })
    ov = _payload("fixed", {"anthropic_plain": [_row("t1", True, 0.0)]})
    bp, op = tmp_path / "base.json", tmp_path / "ov.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    op.write_text(json.dumps(ov), encoding="utf-8")
    rc = MR.main(["--base", str(bp), "--override", str(op), "--out-dir", str(tmp_path / "out"),
                  "--label", "corrected"])
    assert rc == MR.EXIT_OK
    rep = list((tmp_path / "out").glob("report_*_corrected.md"))
    assert rep, list((tmp_path / "out").iterdir())
    text = rep[0].read_text(encoding="utf-8")
    assert "split by pass@1 outcome" in text and "McNemar" in text
    res = json.loads(list((tmp_path / "out").glob("results_*_corrected.json"))[0].read_text(encoding="utf-8"))
    assert res["rows"]["anthropic_plain"][0]["passed"]
    assert res["merged_from"]["base"] == "base"


def test_merge_runs_cli_refuses_a_bad_override_without_writing(tmp_path):
    base = _payload("base", {"anthropic_plain": [_row("t1", True, 0.0)]})
    ov = _payload("ov", {"anthropic_plain": [_row("ghost", True, 0.0)]})
    bp, op = tmp_path / "b.json", tmp_path / "o.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    op.write_text(json.dumps(ov), encoding="utf-8")
    out = tmp_path / "out"
    assert MR.main(["--base", str(bp), "--override", str(op), "--out-dir", str(out)]) == MR.EXIT_ERROR
    assert not out.exists() or not list(out.glob("results_*.json"))


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
