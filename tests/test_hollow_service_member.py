"""FU-236: the anti-hollow rule must be able to SEE a service unit.

Every fixture below marked MOTIVATING is a byte-exact copy of a blob that was
on origin/main (or on an all-green open PR) at the time this was written, and
that the pre-existing `no-hollow` gate passed. The rule is proven RED on those
before it is trusted green on anything -- an assertion never seen fail is not
evidence (HARNESS_DOCTRINE R4).
"""
import ast

import pytest

from zo_sentinel.gates.hollow import (
    hollow_scaffold_scan,
    hollow_service_member_scan,
)

# --- MOTIVATING blobs: real, and every one of them was green ----------------

# main f0146fd2, 75 bytes, added by PR #2744 (14/14 checks pass incl. no-hollow)
FRESHNESS_DASHBOARD = (
    "# First, let me check what the _exemplar looks like and the model structure\n"
)
# main f0146fd2, 61 bytes -- found by the population scan, named in no FU
GROWTH_SNAPSHOT = "# Read services/_exemplar/contract.py to mirror its structure\n"
# main f0146fd2, 54 bytes -- a router that is a comment naming its own path
RISK_TIER_ROUTER = "# services/staged/risk_tier_source_breakdown/router.py\n"
# open PR #2533, 32 bytes, no trailing newline, all-green incl. no-hollow in 6s
CADENCE_JOB_HEALTH = "# services/_exemplar/contract.py"

# A contract with statements but nothing that can fail.
INERT_CONTRACT = (
    '"""Contract for the widget service."""\n'
    "import os\n"
    "NAME = 'widget'\n"
    "def check():\n"
    "    return True\n"
)

# Shape of the 110/113 real contracts on main.
REAL_CONTRACT = (
    '"""Contract for the widget service."""\n'
    "from app.db import get_session\n"
    "\n"
    "def test_rows_exist():\n"
    "    with get_session() as s:\n"
    "        n = s.execute('select count(*) from widgets').scalar()\n"
    "    assert n is not None\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    test_rows_exist()\n"
    "    print('ok')\n"
)


# --- LIMB 1: zero top-level statements --------------------------------------

@pytest.mark.parametrize("name,src", [
    ("services/staged/registry_source_freshness_dashboard/contract.py",
     FRESHNESS_DASHBOARD),
    ("services/staged/mcp_server_registry_growth_snapshot/contract.py",
     GROWTH_SNAPSHOT),
    ("services/staged/risk_tier_source_breakdown/router.py", RISK_TIER_ROUTER),
    ("services/staged/cadence_job_health/contract.py", CADENCE_JOB_HEALTH),
])
def test_motivating_blobs_are_rejected(name, src):
    """Each of these was GREEN under the old gate. All four must now be RED."""
    why = hollow_service_member_scan(name, src)
    assert why is not None, f"{name} must be rejected"
    assert "hollow" in why


def test_the_old_gate_passed_every_motivating_blob():
    """Negative control on the FIX ITSELF, not on the artifacts.

    Reproduces the pre-FU-236 scope guard verbatim. If this ever fails, the
    blobs stopped being the thing that motivated the change and the test above
    is no longer evidence of anything.
    """
    def old_scan(fp, _src):
        return None if ("/" in fp or "\\" in fp) else "would-have-looked"

    for name in ("services/staged/registry_source_freshness_dashboard/contract.py",
                 "services/staged/mcp_server_registry_growth_snapshot/contract.py",
                 "services/staged/risk_tier_source_breakdown/router.py"):
        assert old_scan(name, "") is None


# --- LIMB 2: a contract that cannot fail ------------------------------------

def test_inert_contract_is_rejected():
    why = hollow_service_member_scan(
        "services/staged/widget/contract.py", INERT_CONTRACT)
    assert why is not None and "cannot fail" in why


def test_limb_2_does_not_apply_to_logic_or_router():
    """Only contract.py carries the assert/__main__ obligation."""
    for name in ("services/staged/widget/logic.py",
                 "services/staged/widget/router.py"):
        assert hollow_service_member_scan(name, INERT_CONTRACT) is None


# --- The real population must stay green ------------------------------------

def test_real_contract_passes():
    assert hollow_service_member_scan(
        "services/staged/widget/contract.py", REAL_CONTRACT) is None


def test_contract_with_main_but_no_assert_passes():
    src = ("import os\n"
           'if __name__ == "__main__":\n'
           "    print(os.getcwd())\n")
    assert hollow_service_member_scan(
        "services/staged/widget/contract.py", src) is None


def test_empty_init_is_not_hollow():
    """An empty package marker is correct. Scoping this wrong would block
    nearly every scaffold PR the builder opens."""
    assert hollow_service_member_scan(
        "services/staged/widget/__init__.py", "") is None


def test_non_service_paths_untouched():
    for name in ("app/models.py", "tools/clerk_reconcile.py",
                 "tests/test_x.py", "services/_exemplar/contract.py"):
        assert hollow_service_member_scan(name, FRESHNESS_DASHBOARD) is None


def test_unparseable_source_is_not_this_rules_finding():
    assert hollow_service_member_scan(
        "services/staged/widget/contract.py", "def (:\n") is None


# --- Platform + wiring ------------------------------------------------------

def test_windows_backslash_paths_match():
    """The tower runs Windows and CI runs Linux; a path rule that only matches
    one of them grades a different artifact on each. (Same class as the
    `Path.is_absolute()` split caught on 2026-08-03.)"""
    why = hollow_service_member_scan(
        r"services\staged\registry_source_freshness_dashboard\contract.py",
        FRESHNESS_DASHBOARD)
    assert why is not None


def test_absolute_path_matches():
    why = hollow_service_member_scan(
        "/home/workspace/zo_sentinel/services/staged/widget/contract.py",
        FRESHNESS_DASHBOARD)
    assert why is not None


def test_reaches_all_three_seams_via_the_one_entry_point():
    """goose_runner, the publisher and the CI gate all call
    hollow_scaffold_scan. If the nested rule is not reachable from there, the
    fix arms in a library and nowhere else (R2: a merge is not an arming)."""
    why = hollow_scaffold_scan(
        "services/staged/registry_source_freshness_dashboard/contract.py",
        FRESHNESS_DASHBOARD)
    assert why is not None and "hollow" in why


def test_root_level_rule_still_works():
    """The file-unit rule is unchanged -- this is additive, not a replacement."""
    src = "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/x')\ndef x():\n    return []\n"
    assert hollow_scaffold_scan("thing_api.py", src) is not None
    real = src + "from app.db import get_session\n"
    assert hollow_scaffold_scan("thing_api.py", real) is None


def test_non_py_still_skipped():
    assert hollow_scaffold_scan("services/staged/widget/service.toml", "") is None


# --- The claim about the live population, kept honest -----------------------

def test_the_measurement_is_reproducible_in_principle():
    """Guards the numbers quoted in the module header and in FU-236.

    Not a scan of the repo (that would make this test's runtime depend on
    services/ and it would go red for reasons unrelated to the rule). It pins
    the DISCRIMINATOR instead: the rule must separate, not merely reject.
    """
    hollow_pop = [FRESHNESS_DASHBOARD, GROWTH_SNAPSHOT, RISK_TIER_ROUTER,
                  CADENCE_JOB_HEALTH]
    real_pop = [REAL_CONTRACT]
    p = "services/staged/widget/contract.py"
    assert all(hollow_service_member_scan(p, s) is not None for s in hollow_pop)
    assert all(hollow_service_member_scan(p, s) is None for s in real_pop)


def test_ast_helpers_agree_with_the_rule():
    """`_substantive_body` is the thing the rule turns on; check it directly so
    a failure localises to the helper rather than to the whole scan."""
    from zo_sentinel.gates.hollow import _substantive_body
    assert _substantive_body(FRESHNESS_DASHBOARD) == []
    assert _substantive_body('"""doc only."""\n') == []
    assert _substantive_body("pass\n") == []
    assert len(_substantive_body("x = 1\ny = 2\n")) == 2
    assert _substantive_body("def (:\n") is None
    assert isinstance(ast.parse(REAL_CONTRACT), ast.Module)
