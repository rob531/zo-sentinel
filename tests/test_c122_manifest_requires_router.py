"""c122: a service manifest may not be written before the module it names.

THE DEFECT, measured on origin/main @ 289f1f1ec (2026-09-21) by RUNNING
tools/promote_staged_to_active.py -- not by scanning for its conditions:

    candidates: 1498   promote-eligible: 22   hold: 1476
    1226 of those 1476 HOLDs are the one reason "router.py exposes no router"

tools/service_decomposer.py emits five directives per service. __init__.py and
service.toml are `write_raw` and land deterministically; logic/router/contract
are `generate_file` and land only if an engine succeeds. service.toml declares
`import_path = "services.active.<name>.router"` -- a promise about a file a
LATER, less reliable directive is supposed to write -- and nothing ever
reconciles the promise to reality. So the manifest is the thing that makes an
empty directory COUNT as a service: 1145 of 1499 staged services declare a
router that is not on disk, 251 of the 319 created in the 14d to 2026-09-21,
20 of 20 on 2026-09-20.

THE CURE is ordering by construction, not another gate (harness doctrine R7:
recovery over restriction). The manifest directive declares `requires`, and
goose_runner DEFERS a directive whose requirements are absent -- leaving it
pending, spending no LLM call, ghosting and parking nothing -- so it lands by
itself once router.py exists.

Every assertion below has a NEGATIVE CONTROL in the same test: the input is
mutated so the assertion is observed going RED, because an assertion never seen
red is an untested branch, not evidence (doctrine R4).

No live services, no network. Must pass on Windows and ubuntu-latest.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.service_decomposer import decompose  # noqa: E402
from zo_sentinel.build_completion import unmet_requires  # noqa: E402

SPEC = "GET /api/widget/summary -> counts by tier, read from app.models"


def _by_output(directives, suffix):
    hits = [d for d in directives if d["output_file"].endswith(suffix)]
    assert len(hits) == 1, "expected exactly one %s directive, got %d" % (
        suffix, len(hits))
    return hits[0]


# --------------------------------------------------------------------------
# 1. the producer stamps the dependency -- and only on the manifest
# --------------------------------------------------------------------------

def test_manifest_directive_requires_the_router_it_names():
    ds = decompose("widget_summary", SPEC, prefix="/api/widget")
    toml = _by_output(ds, "/service.toml")

    assert toml["requires"] == ["services/staged/widget_summary/router.py"]

    # The requirement must name the SAME module service.toml's import_path
    # names. A dependency on a different file is the defect wearing a fix.
    assert 'import_path = "services.active.widget_summary.router"' in toml["content"]

    # NEGATIVE CONTROL: without the stamp there is nothing to defer on, which
    # is exactly the pre-c122 behaviour that produced 1145 shells.
    stripped = dict(toml)
    stripped.pop("requires")
    assert unmet_requires(stripped, ROOT) == [], (
        "control failed: a manifest with no `requires` must be unblocked -- if "
        "this is non-empty the helper is inventing a dependency")


def test_only_the_manifest_carries_requires():
    """Inertness. A dependency on the code directives themselves would deadlock:
    router.py cannot require router.py."""
    ds = decompose("widget_summary", SPEC)
    carriers = [d["output_file"] for d in ds if d.get("requires")]
    assert carriers == ["services/staged/widget_summary/service.toml"]

    # NEGATIVE CONTROL: the assertion can distinguish -- seed a second carrier
    # and watch the same comparison fail.
    seeded = [d["output_file"] for d in ds] + ["services/staged/x/router.py"]
    assert seeded != ["services/staged/widget_summary/service.toml"]


def test_router_directive_is_not_self_blocking():
    ds = decompose("widget_summary", SPEC)
    router = _by_output(ds, "/router.py")
    assert unmet_requires(router, ROOT) == []


# --------------------------------------------------------------------------
# 2. the helper -- both poles, on a real temp tree
# --------------------------------------------------------------------------

def test_unmet_requires_is_two_sided(tmp_path):
    svc = tmp_path / "services" / "staged" / "widget_summary"
    svc.mkdir(parents=True)
    directive = {
        "output_file": "services/staged/widget_summary/service.toml",
        "requires": ["services/staged/widget_summary/router.py"],
    }

    # RED pole: the router is absent, so the manifest is blocked.
    assert unmet_requires(directive, str(tmp_path)) == [
        "services/staged/widget_summary/router.py"]

    # GREEN pole: write the router, and the SAME call clears.
    (svc / "router.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n", encoding="utf-8")
    assert unmet_requires(directive, str(tmp_path)) == []

    # RED again: remove it. The check discriminates in both directions, so the
    # green above is a measurement and not a constant.
    (svc / "router.py").unlink()
    assert unmet_requires(directive, str(tmp_path)) == [
        "services/staged/widget_summary/router.py"]


def test_an_empty_router_does_not_satisfy_the_requirement(tmp_path):
    """A 0-byte router.py is the ghost-completion signature, not a router."""
    svc = tmp_path / "services" / "staged" / "s"
    svc.mkdir(parents=True)
    (svc / "router.py").write_text("", encoding="utf-8")
    d = {"output_file": "services/staged/s/service.toml",
         "requires": ["services/staged/s/router.py"]}
    assert unmet_requires(d, str(tmp_path)) == ["services/staged/s/router.py"]


@pytest.mark.parametrize("value", [None, [], "router.py", 17, {"a": 1}])
def test_inert_for_every_directive_that_declares_no_requires(value, tmp_path):
    """The field is additive: every directive already in flight must be
    unaffected, including malformed ones. Only a non-empty LIST blocks."""
    d = {"output_file": "x.py"}
    if value is not None:
        d["requires"] = value
    assert unmet_requires(d, str(tmp_path)) == []


# --------------------------------------------------------------------------
# 3. the executor honours it in the right ORDER
# --------------------------------------------------------------------------

def test_runner_defers_before_spending_anything():
    """Structural control on goose_runner.py.

    Order matters three ways and a future reorder must break this test:
      * BEFORE sl.init_manifest  -- which writes a default-FAIL row, so a
        deferred directive would otherwise be recorded as a failure.
      * BEFORE the write_raw dispatch -- a deferred manifest must never be
        written, nor fall through to the engine (returning False from
        _write_raw_directive hands the file to an LLM, which is the
        68-unparseable-service.toml bug the dispatch was added to stop).
      * It must `continue`, not park or ghost: deferral is recoverable.
    """
    src = open(os.path.join(ROOT, "goose_runner.py"), encoding="utf-8").read()

    assert "unmet_requires" in src, "goose_runner does not consult the helper"
    at_check = src.index("_unmet = unmet_requires(")
    at_manifest = src.index("sl.init_manifest(")
    at_raw = src.index('directive.get("handler") == "write_raw"')
    assert at_check < at_manifest < at_raw

    tail = src[at_check:at_check + 600]
    assert "continue" in tail
    assert "park_directive" not in tail and "_ghost_or_fail" not in tail

    # NEGATIVE CONTROL: the ordering assertion is not vacuous -- reversed, the
    # same comparison must fail.
    assert not (at_manifest < at_check)
