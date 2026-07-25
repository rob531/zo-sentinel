#!/usr/bin/env python3
"""The multi-step ladder build: ONE architect-level service directive fans out
into N single-file directives (the builder's proven lane) via the promoter's
pre-pass. This is the link that makes the SERVICE unit ladder-buildable."""
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

from zo_sentinel.promoters import proposed_to_pending_promoter as P  # noqa: E402


class ServiceFanOutTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def _write(self, name, obj):
        p = self.d / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        # backdate mtime so _iter_proposals age-ordering is stable
        os.utime(p, (1, 1))
        return p

    def test_service_directive_fans_out(self):
        self._write("gen_x_build_service_risk_delta.json", {
            "handler": "build_service", "service_name": "risk_delta",
            "spec": "GET /api/risk/delta returns servers whose risk_tier changed in the "
                    "last N days; reads McpServerRegistry only; response is a JSON list.",
            "prefix": "/api", "task": "build_service_risk_delta",
        })
        n = P._expand_service_directives(self.d)
        self.assertEqual(n, 1)
        children = sorted(f.name for f in self.d.glob("svc_*.json"))
        self.assertEqual(len(children), 5, children)  # __init__, service.toml, logic, router, contract
        # parent renamed .expanded, never re-scanned
        self.assertTrue(any(f.suffix == ".expanded" for f in self.d.iterdir()))
        self.assertFalse((self.d / "gen_x_build_service_risk_delta.json").exists())
        # every child is a VALID single-file directive in the proven lane
        for c in self.d.glob("svc_*.json"):
            d = json.loads(c.read_text())
            ok, why = P._validate(d)
            self.assertTrue(ok, "%s invalid: %s" % (c.name, why))
            self.assertIn(d["handler"], ("generate_file", "write_raw"))
            self.assertTrue(d["output_file"].startswith("services/staged/risk_delta/"))
            self.assertEqual(d["parent_service_directive"], "gen_x_build_service_risk_delta.json")

    def test_bad_service_directive_rejected(self):
        self._write("gen_y_bad.json", {"handler": "build_service", "service_name": "", "spec": "too short"})
        n = P._expand_service_directives(self.d)
        self.assertEqual(n, 0)
        self.assertTrue(any(f.name.endswith(".rejected") for f in self.d.iterdir()))

    def test_kill_switch(self):
        self._write("gen_z_build_service_a.json", {
            "handler": "build_service", "service_name": "a",
            "spec": "x" * 60, "task": "t"})
        os.environ["ZO_SERVICE_UNIT_EXPANSION"] = "0"
        try:
            self.assertEqual(P._expand_service_directives(self.d), 0)
            self.assertEqual(list(self.d.glob("svc_*.json")), [])
        finally:
            del os.environ["ZO_SERVICE_UNIT_EXPANSION"]

    def test_non_service_directives_untouched(self):
        p = self._write("gen_w_normal.json", {"handler": "generate_file", "task": "t",
                                              "output_file": "x.py", "description": "y" * 60})
        P._expand_service_directives(self.d)
        self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
