"""cycle-0092 NEGATIVE CONTROL for the graph_domain_digest wiring.

Every assertion here was observed RED against the pre-change generator
(build_context() had no domain_map key at all, and no code path loaded
tools/graph_domain_digest.py). The bus is never contacted: fetch is replaced,
so the test asserts on the WIRING, not on the graph's current contents.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "zo_sentinel" / "sentinel_directive_generator_goose.py"
DIGEST = ROOT / "tools" / "graph_domain_digest.py"

ROWS = [
    {"community": 3, "modules": 700, "symbols": 7295, "example": "scoring_engine.py"},
    {"community": 9, "modules": 120, "symbols": 1400, "example": "breaker_actions.py"},
]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gen(monkeypatch, tmp_path):
    """Import the generator with its prod side effects redirected at tmp_path."""
    monkeypatch.setenv("ZO_SENTINEL_DIR_TEST", str(tmp_path))
    if not GEN.exists():
        pytest.skip("generator not present")
    try:
        return _load(GEN, "sdgg_under_test")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"generator not importable in this environment: {e}")


def test_the_wiring_literal_exists():
    """The one assertion here that can NEVER skip. RED before cycle-0092: neither
    string appeared anywhere in the generator, which is why the census scored the
    digest dark. dark_tools.py excludes tests/ from its caller corpus, so this
    test can never itself be mistaken for the caller."""
    src = GEN.read_text(encoding="utf-8")
    assert '"tools/graph_domain_digest.py"' in src, (
        "the generator no longer names the digest tool -- the wiring is gone and "
        "the tool is dark again")
    assert '"domain_map"' in src


def test_digest_is_reachable_from_the_generator(gen):
    """The wiring exists AND resolves to a real file -- a path string that does
    not resolve is prose with a slash in it."""
    p = gen._digest_path()
    assert p.exists(), f"{p} does not exist"
    assert p.name == "graph_domain_digest.py"
    mod = gen._load_digest()
    assert hasattr(mod, "format_digest") and hasattr(mod, "SQL")


def test_domain_map_lands_in_the_context(gen, monkeypatch):
    """RED before this cycle: build_context() had no domain_map key."""
    monkeypatch.setattr(gen, "_domain_map", lambda: gen._load_digest().format_digest(ROWS))
    ctx = gen.build_context()
    assert "domain_map" in ctx, "the architect context carries no domain map"
    assert "DOMAIN MAP" in ctx["domain_map"]
    assert "scoring_engine.py" in ctx["domain_map"]
    assert "domain 3: 700 modules" in ctx["domain_map"]


def test_unreachable_bus_costs_a_field_not_a_cycle(gen, monkeypatch):
    """UNKNOWN is not zero (R6): the key is ABSENT, not present-and-empty, and
    build_context() still returns a usable context."""
    def boom(*a, **k):
        raise OSError("bus down")
    monkeypatch.setattr(gen, "_load_digest", boom)
    assert gen._domain_map() == ""
    ctx = gen.build_context()
    assert "domain_map" not in ctx
    assert "schema" in ctx and "already_built_modules" in ctx


def test_empty_rows_are_not_a_pass(gen, monkeypatch):
    """0 rows from the bus is UNKNOWN, not an empty domain list."""
    mod = _load(DIGEST, "gdd_rows_check")
    monkeypatch.setattr(gen, "_load_digest", lambda: mod)
    monkeypatch.setattr(mod, "fetch", lambda *a, **k: [])
    assert gen._domain_map() == ""
