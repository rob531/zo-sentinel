#!/usr/bin/env python3
"""test_graph_native_wiring.py -- guards for the graph-native feedback loop
(Phases 4-5: failure-pattern matrix + escalation). Pure/local: no bus, no DB, no
network, so it runs in CI. The bus round-trip + failure_matrix view query are
covered by the on-box verification, not here.

Asserts:
  - no `import duckdb` anywhere in the build path (CLAUDE.md:250 -- everything
    goes through write_service :8772, never a second DuckDB connection).
  - build_env_for is escalation-aware ONLY behind ZO_ESCALATE: OFF => the pinned
    env, attempt IGNORED, identical to legacy behaviour (zero regression); ON +
    attempt>0 => alias bumped up the ladder, capped at the top FREE rung for
    non-critical work (only complexity=critical may reach the paid critical rung).
  - build_provenance_row produces a deterministic, idempotent build_id (same
    directive/outcome/attempt/day -> same id; success vs ghost never collide) and
    the full build_provenance schema.
"""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zo_sentinel.build_routing import (  # noqa: E402
    build_env_for, build_provenance_row, DEFAULT_ALIAS, _ESCALATION_LADDER)

# Modules that must never open a second DuckDB connection (single-writer rule).
GUARDED = [
    "goose_runner.py", "state_loopback.py",
    "zo_sentinel/build_routing.py", "zo_sentinel/build_completion.py",
    "mcp_servers/builder_mcp.py", "tools/load_graph_to_bus.py",
]


class NoDirectDuckDB(unittest.TestCase):
    def test_no_duckdb_import(self):
        rx = re.compile(r"^\s*(import\s+duckdb|from\s+duckdb\s+import)", re.M)
        for rel in GUARDED:
            p = ROOT / rel
            if p.is_file():
                self.assertIsNone(
                    rx.search(p.read_text(encoding="utf-8")),
                    f"{rel} must not import duckdb -- use write_service :8772")


class EscalationFlag(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ZO_ESCALATE", None)

    def tearDown(self):
        os.environ.pop("ZO_ESCALATE", None)

    def test_off_ignores_attempt(self):
        d = {"complexity": "high"}
        base = build_env_for(d, attempt=0)["GOOSE_MODEL"]
        # Flag off -> attempt is ignored -> identical to the legacy pinned env.
        self.assertEqual(build_env_for(d, attempt=3)["GOOSE_MODEL"], base)
        self.assertEqual(base, DEFAULT_ALIAS)   # high pins to rung-0 today

    def test_on_bumps_and_caps_at_free(self):
        os.environ["ZO_ESCALATE"] = "1"
        d = {"complexity": "high"}
        a1 = build_env_for(d, attempt=1)["GOOSE_MODEL"]
        a9 = build_env_for(d, attempt=9)["GOOSE_MODEL"]
        self.assertNotEqual(a1, DEFAULT_ALIAS)           # it climbed
        self.assertEqual(a9, "zo-ladder-high")           # capped at top FREE rung
        self.assertNotEqual(a9, "zo-ladder-critical")    # never paid for non-critical

    def test_critical_may_reach_paid_rung(self):
        os.environ["ZO_ESCALATE"] = "1"
        d = {"complexity": "critical"}
        self.assertEqual(
            build_env_for(d, attempt=9)["GOOSE_MODEL"], "zo-ladder-critical")

    def test_ladder_order(self):
        # low -> medium -> high -> critical, cost-ordered (escalation.py rungs).
        self.assertEqual(
            _ESCALATION_LADDER,
            ["zo-ladder-low", "zo-ladder-medium", "zo-ladder-high", "zo-ladder-critical"])


class ProvenanceRow(unittest.TestCase):
    def test_idempotent_id_and_schema(self):
        kw = dict(directive_id="dir1", directive_type="enricher", complexity="high",
                  model="zo-ladder-high", rescue_count=2, attempt=2)
        r1 = build_provenance_row(success=False, smoke_result="ghost", error="boom",
                                  built_at="2026-06-09T00:00:00+00:00", **kw)
        r2 = build_provenance_row(success=False, smoke_result="ghost", error="boom",
                                  built_at="2026-06-09T12:00:00+00:00", **kw)
        # Same directive/outcome/attempt/day -> same build_id (idempotent INSERT OR IGNORE).
        self.assertEqual(r1["build_id"], r2["build_id"])
        # Success vs ghost on the same attempt must NOT collide.
        r3 = build_provenance_row(success=True, smoke_result="pass",
                                  built_at="2026-06-09T13:00:00+00:00", **kw)
        self.assertNotEqual(r1["build_id"], r3["build_id"])
        for col in ("build_id", "directive_id", "directive_type", "complexity",
                    "engine", "model", "backend", "smoke_result", "rescue_count",
                    "success", "output_path", "output_bytes", "error", "built_at"):
            self.assertIn(col, r1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
