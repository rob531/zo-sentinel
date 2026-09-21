"""Negative control for the salvaged-service-directive alias (daily-chairman-review 2026-07-30).

These tests are written to FAIL on the parent commit. That is the point: the
validator they cover had never been observed red for this input, and 207
.rejected files were the invisible consequence. Verified red on 17cea7c
(3 failures) and green with the fix.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
MOD = REPO / "zo_sentinel" / "promoters" / "proposed_to_pending_promoter.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p2p_promoter_alias", MOD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


SPEC = (
    "GET /api/risk/tier_history?server_id=STRING&days=INT on prefix /api. Reads "
    "mcp_llm_axis_scores and mcp_server_registry via write_service query, orders by "
    "scored_at and returns {server_id, days, tier_changes:[{date,tier,reason}]}. "
    "Acceptance: __main__ seeds an in-memory SQLite and asserts 200."
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("build_service_risk_tier_history", "risk_tier_history"),
        ("build_risk_tier_summary_api", "risk_tier_summary_api"),
        ("plain_service_name", "plain_service_name"),
    ],
)
def test_task_key_yields_a_prefix_stripped_service_name(raw, expected):
    """The SALVAGE emitter writes {"task": "build_service_<svc>"}, not service_name."""
    mod = _load()
    name, src = mod._service_name_of({"handler": "build_service", "task": raw})
    assert name == expected, "task alias must strip the directive prefix"
    assert src == "task"


def test_explicit_service_name_still_wins_over_task():
    mod = _load()
    name, src = mod._service_name_of(
        {"service_name": "canonical", "task": "build_service_other"}
    )
    assert (name, src) == ("canonical", "service_name")


def test_unusable_directive_is_still_reported_as_unusable():
    """Guard the other direction: the check must still be able to go RED."""
    mod = _load()
    assert mod._service_name_of({"handler": "build_service"}) == ("", "")
    assert mod._service_name_of({"task": "build_service_"}) == ("", "")


def test_salvage_shaped_directive_expands_instead_of_being_rejected(tmp_path):
    """End-to-end: the exact on-disk shape of the 195 rejected files."""
    mod = _load()
    proposed = tmp_path / "proposed"
    proposed.mkdir()
    salvaged = {
        "task": "build_service_risk_tier_history",
        "handler": "build_service",
        "complexity": "medium",
        "priority": 0.75,
        "description": SPEC,
        "rationale": "SALVAGED from the architect transcript",
    }
    p = proposed / "salvage_20260730115916_build_service_risk_tier_history.json"
    p.write_text(json.dumps(salvaged), encoding="utf-8")

    n = mod._expand_service_directives(proposed)

    assert not p.with_name(p.name + ".rejected").exists(), (
        "a salvaged service directive with a 400-char spec must not be rejected "
        "for using the 'task' key"
    )
    assert n == 1
    assert p.with_name(p.name + ".expanded").exists()
    children = list(proposed.glob("svc_*.json"))
    assert children, "fan-out produced no child directives"
    assert any("risk_tier_history" in c.name for c in children)
    assert not any("build_service_risk_tier_history" in c.name for c in children), (
        "service must not land at services/staged/build_service_<name>/"
    )
