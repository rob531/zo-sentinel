#!/usr/bin/env python3
"""Tests for the FU-031 model-name casing linter (tools/model_import_linter.py)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import model_import_linter as L  # noqa: E402


class LinterTests(unittest.TestCase):
    def setUp(self):
        self.norm_map = L.build_map({"McpServerRegistry", "McpLlmAxisScore", "McpScoreDispute"})

    def test_detects_casing_and_spurious_plural(self):
        src = ("from app.models import MCPServerRegistry, McpLlmAxisScores\n"
               "x = MCPServerRegistry\n")
        drift = L.scan_text(src, self.norm_map)
        self.assertEqual(drift.get("MCPServerRegistry"), "McpServerRegistry")
        self.assertEqual(drift.get("McpLlmAxisScores"), "McpLlmAxisScore")

    def test_all_caps_llm_variant(self):
        drift = L.scan_text("import MCPLLMAxisScore\n", self.norm_map)
        self.assertEqual(drift.get("MCPLLMAxisScore"), "McpLlmAxisScore")

    def test_no_false_positive_on_common_names(self):
        # canon is Mcp*-only, so short/common identifiers must never be touched
        src = "user = db.query(User)\nrouter = APIRouter()\nBase.metadata.create_all()\n"
        self.assertEqual(L.scan_text(src, self.norm_map), {})

    def test_correct_spelling_is_not_flagged(self):
        src = "from app.models import McpServerRegistry, McpLlmAxisScore\n"
        self.assertEqual(L.scan_text(src, self.norm_map), {})

    def test_fix_rewrites_in_place(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "m.py")
        with open(p, "w") as fh:
            fh.write("from app.models import MCPServerRegistry\nq = MCPServerRegistry\n")
        res = L.lint_file(p, self.norm_map, fix=True)
        self.assertTrue(res["fixed"])
        out = open(p).read()
        self.assertIn("McpServerRegistry", out)
        self.assertNotIn("MCPServerRegistry", out)

    def test_canonical_models_from_real_models_py(self):
        canon = L.canonical_models()
        self.assertIn("McpServerRegistry", canon)
        # every canonical name is distinctive (Mcp-prefixed, >=8 chars)
        for c in canon:
            self.assertTrue(c.startswith("Mcp") and len(c) >= 8, c)


if __name__ == "__main__":
    unittest.main()
