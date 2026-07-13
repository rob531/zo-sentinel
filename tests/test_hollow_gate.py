"""The anti-hollow rule: one definition, enforced at three seams.

These tests exist because of HOW this gate failed, twice:
  * it was stated in a PROMPT STRING to the builder and never enforced -> ~14% of
    builds shipped hollow anyway (Exemplar Doctrine: enforce in code, not prose);
  * it was then enforced at the publisher (#1450) but NOT at the builder, so the
    build tokens were still burned and the .done sentinel still blocked the retry.
So we assert not just that the rule is correct, but that every seam actually CALLS
it and that nobody has quietly re-inlined a second copy of the patterns.
"""
import importlib.util
import io
import os

from zo_sentinel.gates import hollow
from zo_sentinel.gates.hollow import hollow_scaffold_scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REAL_MODULE = (
    "from fastapi import FastAPI\n"
    "from app.db import get_session\n"
    "from app.models import McpServer\n"
    "app = FastAPI()\n"
    "@app.get('/x')\n"
    "def x():\n"
    "    with get_session() as s:\n"
    "        return s.query(McpServer).count()\n"
)


# --- the rule ---------------------------------------------------------------

def test_mock_data_layer_is_hollow():
    assert hollow_scaffold_scan("x.py", "# Mock data\nrows = []\n")
    assert hollow_scaffold_scan("x.py", "class MockDB:\n    pass\n")
    assert hollow_scaffold_scan("x.py", "# placeholder until the DB lands\n")


def test_standalone_api_without_real_data_layer_is_hollow():
    why = hollow_scaffold_scan("x.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    assert why and "no real data layer" in why


def test_module_bound_to_the_real_data_layer_passes():
    assert hollow_scaffold_scan("verdict_breakdown_api.py", REAL_MODULE) is None


def test_non_api_module_passes():
    assert hollow_scaffold_scan("x.py", "def add(a, b):\n    return a + b\n") is None


def test_scope_is_root_level_py_only():
    # package code legitimately defines routers; CI only ever sees ADDED root
    # modules, so a stricter scan here would block work CI would happily merge.
    assert hollow_scaffold_scan("app/routes.py", "app = FastAPI()") is None
    assert hollow_scaffold_scan("zo_sentinel/x.py", "app = FastAPI()") is None
    assert hollow_scaffold_scan("x.html", "mock data") is None


# --- the seams: all three must use the SAME rule object ---------------------

def test_publisher_uses_the_shared_rule():
    from zo_sentinel.publisher import publisher
    assert publisher.hollow_scaffold_scan is hollow_scaffold_scan


def test_ci_gate_uses_the_shared_rule():
    spec = importlib.util.spec_from_file_location(
        "no_hollow_scaffold", os.path.join(ROOT, "tests", "ci", "no_hollow_scaffold.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.hollow_scaffold_scan is hollow_scaffold_scan


def test_builder_completion_chains_all_call_the_gate():
    """Every path that can mark a build .done must run the gate.

    goose_runner has two completion chains (goose build + deterministic engine
    fallback). A gate wired into only one of them is a hole -- that is precisely
    the bug class this suite guards.
    """
    src = io.open(os.path.join(ROOT, "goose_runner.py"), encoding="utf-8").read()
    chains = src.count("and _schema_prm_gate(directive, directive_id)")
    gated = src.count("and _no_hollow_gate(directive, directive_id)")
    assert chains >= 2, "expected the builder's completion chains to still exist"
    assert gated == chains, f"{chains - gated} completion chain(s) skip the no-hollow gate"


def test_the_gate_records_a_lesson_so_the_retry_is_grounded():
    src = io.open(os.path.join(ROOT, "goose_runner.py"), encoding="utf-8").read()
    body = src.split("def _no_hollow_gate(")[1].split("\ndef ")[0]
    assert 'record_lesson(' in body and '"no_hollow"' in body, (
        "a blocked build that teaches the builder nothing will just be rebuilt hollow")


# --- drift guard: exactly ONE copy of the patterns in the tree ---------------

def test_patterns_are_defined_in_exactly_one_place():
    """One rule, one definition. A second copy is how gates drift apart."""
    needle = r"class\s+Mock|MockDB"   # literal source text of the MOCK pattern
    hits = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules",
                                                "archive", "quarantine", "_scratch")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            if os.path.abspath(path) == os.path.abspath(__file__):
                continue   # this suite quotes the needle to look for it
            try:
                src = io.open(path, encoding="utf-8").read()
            except OSError:
                continue
            if needle in src:
                hits.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    assert hits == ["zo_sentinel/gates/hollow.py"], (
        f"the hollow patterns must live in ONE module; found copies in: {hits}")


def test_gate_is_flippable_and_defaults_on():
    from zo_sentinel import policy
    assert policy.KEYS["builder.no_hollow_gate"]["default"] is True