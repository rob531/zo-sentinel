"""The queue is NEVER empty -- enforced in code, tested here.

CofC 2026-07-14 (P0). On 7/14 the factory was found with proposed=0, pending=0
and the builder idle for 178 consecutive cycles -- no build PR in 13 hours. The
architect had been returning +0 for a day: it burned its turn budget on read_*
tools re-fetching context it had ALREADY been handed, and never reached
propose_directive.

"Directives must NEVER be empty" was a rule enforced by a human noticing and
hand-seeding. That IS the failure -- the same class as a rescore that only runs
when someone remembers to fire it, and the same class as the uncalled is_fresh()
helper in #1467.

These tests pin the two properties that matter:
  1. When the queue is EMPTY, the floor seeds it -- WITHOUT the architect.
  2. When the queue has work, the floor stays DORMANT (it is a floor, not a
     second architect competing with the real one).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def gen(tmp_path, monkeypatch):
    """Import the generator with SENTINEL_DIR pointed at a temp tree."""
    sentinel = tmp_path / "zo_sentinel"
    (sentinel / "directives" / "proposed").mkdir(parents=True)
    (sentinel / "directives" / "pending").mkdir(parents=True)
    (sentinel / "goose_recipes").mkdir(parents=True)
    # a spec with an unbuilt, spec-named target the miner can find
    (sentinel / "PRODUCT_SPEC.md").write_text(
        "## Roadmap\n\n"
        "The fleet needs a rollup: `axis_rollup_api.py` is a directive candidate "
        "-- a read-only FastAPI router aggregating mcp_llm_axis_scores by axis so "
        "the dashboard can render fleet composition without a full scan.\n",
        encoding="utf-8")

    monkeypatch.setenv("DGG_STARVATION_FLOOR", "1")
    monkeypatch.setenv("DGG_FLOOR_SEED_N", "2")

    sys.modules.pop("zo_sentinel.sentinel_directive_generator_goose", None)
    m = importlib.import_module("zo_sentinel.sentinel_directive_generator_goose")
    importlib.reload(m)
    monkeypatch.setattr(m, "SENTINEL_DIR", sentinel)
    monkeypatch.setattr(m, "PROPOSED_DIR", sentinel / "directives" / "proposed")
    monkeypatch.setattr(m, "PENDING_DIR", sentinel / "directives" / "pending")
    monkeypatch.setattr(m, "FLOOR_ON", True)
    monkeypatch.setattr(m, "FLOOR_SEED_N", 2)
    return m


def test_floor_seeds_when_queue_is_empty(gen):
    """THE regression: proposed=0 + pending=0 must NOT survive a cycle."""
    assert gen._count_proposed() == 0 and gen._count_pending() == 0

    n = gen._starvation_floor()

    assert n >= 1, "an empty queue must be refilled without the architect"
    assert gen._count_proposed() >= 1
    seeded = list(gen.PROPOSED_DIR.glob("floor_*.json"))
    assert seeded, "floor seeds should be identifiable as floor seeds"

    d = json.loads(seeded[0].read_text())
    assert d["handler"] == "generate_file"
    assert d["output_file"].endswith(".py")
    # a thin description GHOSTS -- the builder builds from `description` alone
    assert len(d["description"]) >= 200
    # and it must be grounded in the real spec paragraph, not boilerplate
    assert "axis_rollup" in d["description"] or "axis_rollup" in d["task"]


def test_floor_is_dormant_when_the_queue_has_work(gen):
    """It is a FLOOR, not a second architect. Work present => do nothing."""
    (gen.PROPOSED_DIR / "real_directive.json").write_text('{"task": "x"}')
    before = gen._count_proposed()

    assert gen._starvation_floor() == 0
    assert gen._count_proposed() == before
    assert not list(gen.PROPOSED_DIR.glob("floor_*.json"))


def test_pending_work_alone_keeps_the_floor_dormant(gen):
    """The builder eats from pending/. Work there means it is NOT starving."""
    (gen.PENDING_DIR / "queued.json").write_text('{"task": "y"}')

    assert gen._starvation_floor() == 0
    assert not list(gen.PROPOSED_DIR.glob("floor_*.json"))


def test_floor_does_not_reseed_what_is_already_queued(gen):
    """Idempotent: a second empty-queue pass must not duplicate the same task."""
    gen._starvation_floor()
    first = {p.name for p in gen.PROPOSED_DIR.glob("floor_*.json")}
    tasks_first = {json.loads(p.read_text())["task"]
                   for p in gen.PROPOSED_DIR.glob("floor_*.json")}

    # queue is no longer empty, so the floor must stay dormant
    assert gen._starvation_floor() == 0
    tasks_now = {json.loads(p.read_text())["task"]
                 for p in gen.PROPOSED_DIR.glob("floor_*.json")}
    assert tasks_now == tasks_first
    assert {p.name for p in gen.PROPOSED_DIR.glob("floor_*.json")} == first


def test_floor_can_be_disabled(gen, monkeypatch):
    monkeypatch.setattr(gen, "FLOOR_ON", False)
    assert gen._starvation_floor() == 0
    assert gen._count_proposed() == 0


def test_recipe_does_not_order_a_read_before_proposing():
    """The recipe used to say 'CALL THIS FIRST' (a read) in three places and end
    with 'Execute step 1 immediately' -- step 1 being a read. That contradiction
    is what burned the turn budget. It must not come back."""
    root = Path(__file__).resolve().parents[1]
    y = (root / "goose_recipes" / "directive_architect.yaml").read_text(
        encoding="utf-8")
    assert "CALL THIS FIRST" not in y
    assert "Execute step 1 immediately" not in y
    assert "FIRST tool call MUST be zo_directive_bridge__propose_directive" in y


# ---------------------------------------------------------------------------
# Filter hardening -- from the floor's FIRST LIVE FIRE (2026-07-14 17:02).
# It correctly caught an empty queue and seeded within one cycle... with junk:
#   foo_bar               <- a placeholder name in PRODUCT_SPEC.md:149
#   anchor_refill         <- ALREADY EXISTS at zo_sentinel/anchor_refill.py
#   tier1_inline_enricher <- "enricher": the DEPRECATED class the recipe forbids
# A floor that seeds junk is worse than no floor: it manufactures hollow builds.
# ---------------------------------------------------------------------------

def test_placeholder_names_are_never_seeded(gen):
    """`foo_bar` is spec PROSE, not a build target."""
    for junk in ("foo_bar.py", "foo.py", "example.py", "template.py", "main.py"):
        assert not gen._is_seedworthy(junk), junk


def test_deprecated_classes_are_never_seeded(gen):
    """The SFT student model OWNS scoring. Hand-built enrichers are dead work --
    the recipe forbids the ARCHITECT from proposing them, so the floor must not
    become a back door around that rule."""
    for dead in ("tier1_inline_enricher.py", "domain_trust_enrichment.py",
                 "signal_analyser_v2.py", "tool_fingerprint_scan.py"):
        assert not gen._is_seedworthy(dead), dead


def test_frontend_is_never_seeded(gen):
    """FE/.html and the app spine are AGENT-built in-session; they ghost."""
    assert not gen._is_seedworthy("admin_dashboard_view.html")


def test_real_targets_are_seedworthy(gen):
    for good in ("vuln_alias_resolver.py", "server_score_staleness_api.py",
                 "unscored_registry_gap_api.py"):
        assert gen._is_seedworthy(good), good


def test_modules_existing_in_a_subpackage_are_excluded(gen, tmp_path, monkeypatch):
    """anchor_refill.py lives at zo_sentinel/anchor_refill.py. The miner's
    _disk_names() only scans the TOP level, so the floor 'discovered' a module
    that had existed for months. _existing_anywhere() must recurse."""
    sentinel = gen.SENTINEL_DIR
    pkg = sentinel / "zo_sentinel"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "anchor_refill.py").write_text("# exists", encoding="utf-8")

    names = gen._existing_anywhere()
    assert "anchor_refill.py" in names and "anchor_refill" in names


def test_floor_seeds_nothing_rather_than_seeding_junk(gen, monkeypatch):
    """If every candidate is junk, seed NOTHING and say so loudly. An empty
    queue is bad; a queue full of hollow builds is worse."""
    monkeypatch.setattr(gen, "_is_seedworthy", lambda f: False)
    assert gen._starvation_floor() == 0
    assert not list(gen.PROPOSED_DIR.glob("floor_*.json"))
