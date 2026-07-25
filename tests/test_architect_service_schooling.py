#!/usr/bin/env python3
"""The architect's schooling: it can now PROPOSE the service unit end-to-end.
Chain under test: recipe vocabulary -> directive_mcp write-time validation ->
promoter fan-out accepts the stamped shape."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GOOD_SPEC = ("GET /api/risk/trend?days=N on prefix /api. logic.py reads mcp_llm_axis_scores "
             "joined to mcp_server_registry and counts per-day tier transitions; router returns "
             "{days, series}. ACCEPTANCE: contract seeds rows in SQLite, asserts 200, prints PASS.")


def _load_validate():
    """Import directive_mcp._validate without the module's fastmcp side effects
    if possible; else skip (mcp package absent in some CI contexts)."""
    try:
        from zo_sentinel.mcp_servers.directive_mcp import _validate  # noqa
        return _validate
    except (ImportError, SystemExit):
        return None


class WriteTimeValidatorTests(unittest.TestCase):
    def setUp(self):
        self.v = _load_validate()
        if self.v is None:
            self.skipTest("mcp package unavailable -- source-level tests still cover this")

    def test_good_build_service_accepted(self):
        ok, why = self.v({"task": "build_service_risk_tier_trend", "handler": "build_service",
                          "description": GOOD_SPEC})
        self.assertTrue(ok, why)

    def test_short_spec_rejected(self):
        ok, why = self.v({"task": "build_service_x", "handler": "build_service",
                          "description": "too short"})
        self.assertFalse(ok)
        self.assertIn("spec", why)

    def test_bad_task_name_rejected(self):
        ok, why = self.v({"task": "make_me_a_service", "handler": "build_service",
                          "description": GOOD_SPEC})
        self.assertFalse(ok)

    def test_existing_service_rejected(self):
        # any registered live service name collides (seeded active/ has these)
        ok, why = self.v({"task": "build_service_verdict_breakdown_api",
                          "handler": "build_service", "description": GOOD_SPEC})
        self.assertFalse(ok)
        self.assertIn("already exists", why)


class ChainShapeTests(unittest.TestCase):
    def test_stamped_directive_fans_out(self):
        """The dict propose_directive writes (service_name stamped) is exactly what
        the promoter's fan-out consumes."""
        from zo_sentinel.promoters import proposed_to_pending_promoter as P
        d = Path(tempfile.mkdtemp())
        j = {"task": "build_service_risk_tier_trend", "handler": "build_service",
             "description": GOOD_SPEC, "service_name": "risk_tier_trend"}
        p = d / "gen_a_build_service_risk_tier_trend.json"
        p.write_text(json.dumps(j)); os.utime(p, (1, 1))
        self.assertEqual(P._expand_service_directives(d), 1)
        self.assertEqual(len(list(d.glob("svc_*.json"))), 5)

    def test_source_level_schooling_present(self):
        mcp_src = open(os.path.join(REPO_ROOT, "zo_sentinel/mcp_servers/directive_mcp.py")).read()
        self.assertIn('"build_service"', mcp_src)            # handler accepted
        self.assertIn("SERVICE UNIT", mcp_src)               # tool docstring teaches it
        recipe = open(os.path.join(REPO_ROOT, "goose_recipes/directive_architect.yaml")).read()
        self.assertIn("THE SERVICE UNIT", recipe)            # prompt teaches it
        self.assertIn("build_service_<snake_name>", recipe)
        self.assertIn("GOLD STANDARD", recipe)
        runner = open(os.path.join(REPO_ROOT, "goose_runner.py")).read()
        self.assertIn("service_dir_from_exemplar", runner)   # children's recipe allowlisted


if __name__ == "__main__":
    unittest.main()


class RejectionTimeSteeringTests(unittest.TestCase):
    """The pedagogy that provably lands: single-file HTTP-surface proposals are
    rejected WITH the re-propose recipe; non-surface single-file work passes."""

    def setUp(self):
        self.v = _load_validate()
        if self.v is None:
            self.skipTest("mcp package unavailable")

    def test_single_file_router_redirected(self):
        ok, why = self.v({"task": "build_tier_summary_api", "handler": "generate_file",
                          "output_file": "tier_summary_api.py",
                          "description": "FastAPI router exposing GET /api/tiers via APIRouter, "
                                         "reads mcp_llm_axis_scores, returns counts. " + "x" * 140})
        self.assertFalse(ok)
        self.assertIn("build_service_<snake_name>", why)   # the lesson is IN the rejection

    def test_consumer_without_route_passes_lane(self):
        ok, why = self.v({"task": "build_tier_rollup_consumer", "handler": "generate_file",
                          "output_file": "tier_rollup_consumer.py",
                          "description": "Batch consumer reading mcp_llm_axis_scores and writing "
                                         "risk_tier rollups via the app session; no HTTP surface; "
                                         "__main__ self-test seeds SQLite and prints PASS. " + "x" * 100})
        self.assertTrue(ok, why)

    def test_kill_switch(self):
        os.environ["ZO_SERVICE_UNIT_REDIRECT"] = "0"
        try:
            ok, _ = self.v({"task": "build_some_api", "handler": "generate_file",
                            "output_file": "some_api.py",
                            "description": "APIRouter GET /api/x reads mcp_llm_axis_scores, "
                                           "returns counts by tier; ACCEPTANCE: __main__ "
                                           "TestClient asserts 200 and prints PASS. " + "y" * 200})
            self.assertTrue(ok)
        finally:
            del os.environ["ZO_SERVICE_UNIT_REDIRECT"]

    def test_build_service_not_redirected(self):
        ok, why = self.v({"task": "build_service_tier_summary", "handler": "build_service",
                          "description": GOOD_SPEC})
        self.assertTrue(ok, why)
