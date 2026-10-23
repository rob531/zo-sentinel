"""FU-349 / issue #3415: service_decomposer must emit unambiguous staged paths
and refuse reserved service names.

Background: a build_service directive literally named "contract" was fanned out;
its scaffold-init directive said "Create the package __init__.py for service
'contract'" and a goose tier-1 agent resolved that to the REPO ROOT
zo_sentinel/__init__.py, coupling zo_sentinel to app and killing every
`python -m zo_sentinel.*` entrypoint (3-day outage + 08-22 regression).

Two cures, both tested here:
1. decompose() raises ValueError for concern-word / load-bearing / malformed
   names, at the single choke point every caller goes through.
2. The write_raw scaffold descriptions name the EXACT repo-relative target path
   and explicitly forbid the repo-root markers.

Must pass on Windows and the ubuntu-latest GH runner - no live services.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.service_decomposer import (  # noqa: E402
    RESERVED_SERVICE_NAMES,
    decompose,
    validate_service_name,
)
from zo_sentinel.promoters import proposed_to_pending_promoter as promoter  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Reserved / malformed names are refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", sorted(RESERVED_SERVICE_NAMES))
def test_reserved_names_raise(bad):
    with pytest.raises(ValueError):
        validate_service_name(bad)
    with pytest.raises(ValueError):
        decompose(bad, "x" * 60)


@pytest.mark.parametrize("bad", ["", "Contract", "risk-delta", "9lives", "a b"])
def test_malformed_names_raise(bad):
    with pytest.raises(ValueError):
        decompose(bad, "x" * 60)


def test_good_name_still_decomposes():
    """Positive control: the latch must not reject a legitimate service."""
    out = decompose("risk_delta", "GET /api/risk/delta returns the risk delta " * 3)
    assert len(out) == 5
    tasks = {d["task"] for d in out}
    assert "scaffold_risk_delta_init" in tasks
    assert "build_risk_delta_router" in tasks


# ---------------------------------------------------------------------------
# 2. Every emitted path is inside services/staged/<name>/ and the write_raw
#    descriptions name the exact path + forbid the root markers
# ---------------------------------------------------------------------------


def test_all_output_files_are_staged():
    for d in decompose("risk_delta", "spec text long enough to pass validation " * 2):
        assert d["output_file"].startswith("services/staged/risk_delta/"), d["output_file"]


def test_init_description_is_unambiguous():
    init = [d for d in decompose("risk_delta", "x" * 60)
            if d["task"] == "scaffold_risk_delta_init"][0]
    desc = init["description"]
    # the literal exact path an agent cannot mis-resolve
    assert "services/staged/risk_delta/__init__.py" in desc
    # the explicit prohibition on the two markers that took the pipeline down
    assert "zo_sentinel/__init__.py" in desc
    assert "app/__init__.py" in desc
    assert "Do NOT" in desc


def test_service_toml_description_names_exact_path():
    toml = [d for d in decompose("risk_delta", "x" * 60)
            if d["task"] == "scaffold_risk_delta_service_toml"][0]
    assert "services/staged/risk_delta/service.toml" in toml["description"]


# ---------------------------------------------------------------------------
# 3. Promoter fan-out rejects a reserved-name build_service directive
#    (the exact shape that produced the outage) instead of expanding it
# ---------------------------------------------------------------------------


def _build_service_directive(name: str) -> dict:
    return {
        "task": "build_service_%s" % name,
        "handler": "build_service",
        "service_name": name,
        "spec": "A service spec easily longer than the fifty character minimum "
                "so only the name can be the reason for rejection.",
    }


def test_promoter_rejects_reserved_name(tmp_path):
    proposed = tmp_path / "proposed"
    proposed.mkdir()
    p = proposed / "build_service_contract.json"
    p.write_text(json.dumps(_build_service_directive("contract")), encoding="utf-8")

    expanded = promoter._expand_service_directives(proposed)

    assert expanded == 0
    assert not p.exists()
    assert (proposed / "build_service_contract.json.rejected").exists()
    assert list(proposed.glob("svc_*.json")) == []


def test_promoter_still_expands_good_name(tmp_path):
    """Positive control at the call site: a real service still fans out."""
    proposed = tmp_path / "proposed"
    proposed.mkdir()
    p = proposed / "build_service_risk_delta.json"
    p.write_text(json.dumps(_build_service_directive("risk_delta")), encoding="utf-8")

    expanded = promoter._expand_service_directives(proposed)

    assert expanded == 1
    assert (proposed / "build_service_risk_delta.json.expanded").exists()
    children = sorted(c.name for c in proposed.glob("svc_*.json"))
    assert len(children) == 5
