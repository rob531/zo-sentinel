#!/usr/bin/env python3
"""Tests for the SOA build-time spine (tools/generate_spine.py + the emitted
app/_spine_generated.py). Pure stdlib unittest -- runs under pytest too.

Covers:
  * the seed of services/active/ is strict-CLEAN (no unlisted-broken, no stale
    known-issue) -- the satisfiable-gate contract;
  * the committed app/_spine_generated.py is IN SYNC with services/active/;
  * spine_known_issues.json is truthful (no stale static entries);
  * the EMITTED include_spine() fail-loud buckets behave: a good service mounts,
    a router-less module is a VISIBLE skip, an import-crashing module is a
    recorded failure (not a swallow), and strict=True raises.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import generate_spine as G  # noqa: E402


class SeedContractTests(unittest.TestCase):
    def test_seed_is_strict_clean(self):
        m = G.build_manifest()
        self.assertEqual(
            m["unlisted_broken"], [],
            "services/active/ has a broken service not in spine_known_issues.json: "
            + repr([(b["name"], b["status"]) for b in m["unlisted_broken"]]))
        self.assertEqual(
            m["stale_known"], [],
            "spine_known_issues.json has STALE entries (now healthy/absent): "
            + repr(m["stale_known"]))

    def test_generated_file_in_sync(self):
        m = G.build_manifest()
        self.assertTrue(
            G.check_in_sync(m),
            "app/_spine_generated.py is STALE vs services/active/ -- run "
            "`python tools/generate_spine.py --emit .`")

    def test_every_active_service_has_import_path(self):
        for s in G.build_manifest()["services"]:
            self.assertTrue(s.get("import_path"),
                            "active service %s has no import_path (NO_TOML?)" % s["name"])


class _FakeApp:
    """Minimal stand-in for FastAPI: records include_router + carries .state."""
    def __init__(self):
        self.state = types.SimpleNamespace()
        self.included = []

    def include_router(self, r):
        self.included.append(r)


class IncludeSpineBucketTests(unittest.TestCase):
    """Exercise the ACTUAL emitted include_spine() against synthetic modules."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sys.path.insert(0, self.tmp)
        # good service: exposes a truthy `router`
        self._write("good_svc", "router = object()\n")
        # router-less: importable but no `router` attribute
        self._write("norouter_svc", "x = 1\n")
        # import-crashing: raises at import time
        self._write("badimport_svc", "raise ImportError('boom: missing model')\n")

    def tearDown(self):
        if self.tmp in sys.path:
            sys.path.remove(self.tmp)
        for name in ("good_svc", "norouter_svc", "badimport_svc"):
            sys.modules.pop(name, None)

    def _write(self, name, body):
        with open(os.path.join(self.tmp, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(body)

    def _emit_include_spine(self):
        manifest = {"services": [
            {"name": "good_svc", "import_path": "good_svc", "prefix": None, "origin": "service"},
            {"name": "norouter_svc", "import_path": "norouter_svc", "prefix": None, "origin": "service"},
            {"name": "badimport_svc", "import_path": "badimport_svc", "prefix": None, "origin": "service"},
        ]}
        code = G.render_generated(manifest)
        ns: dict = {}
        exec(compile(code, "<generated_spine>", "exec"), ns)  # noqa: S102 -- our own emitted code
        return ns["include_spine"]

    def test_buckets(self):
        include_spine = self._emit_include_spine()
        app = _FakeApp()
        result = include_spine(app)  # strict=False -> boot anyway
        self.assertEqual(result["mounted"], ["good_svc"])
        self.assertEqual(result["skipped_no_router"], ["norouter_svc"])
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["service"], "badimport_svc")
        self.assertIn("boom", result["failures"][0]["error"])
        # buckets recorded on app.state for /spine/health
        self.assertEqual(app.state.spine_mounted, ["good_svc"])
        self.assertEqual(app.state.spine_skipped_no_router, ["norouter_svc"])
        self.assertEqual(len(app.state.spine_mount_failures), 1)
        # exactly one router actually included
        self.assertEqual(len(app.included), 1)

    def test_strict_raises_on_failure(self):
        include_spine = self._emit_include_spine()
        with self.assertRaises(RuntimeError):
            include_spine(_FakeApp(), strict=True)


if __name__ == "__main__":
    unittest.main()
