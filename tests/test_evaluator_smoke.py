"""Smoke tests for the Goose-T2 cheap evaluator reverse-feed.

These tests cover only the pure-Python pieces of the reverse-feed loop:
row construction, junit summarisation, and idempotency-set parsing. They
must run on any vanilla GH-hosted runner — no /home/workspace path,
no live write_service, no real `gh` CLI calls.

The fetcher's network/subprocess paths are covered separately on the
tower (manually or by the scheduled fetch-failures workflow).
"""
from __future__ import annotations

import json
import sys
import textwrap
import unittest
from pathlib import Path

# Make the repo root importable regardless of pytest invocation cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.evaluators import gh_actions_fetcher as ghf  # noqa: E402


class TestBuildRow(unittest.TestCase):
    """build_row() must produce a mesh_memory-shaped dict the write path
    accepts, with all the fields read_failure_history expects."""

    def _sample_run(self) -> dict:
        return {
            "databaseId":   123456789,
            "name":         "evaluator",
            "workflowName": "evaluator",
            "conclusion":   "failure",
            "headBranch":   "feature/example",
            "headSha":      "abc1234def5678",
            "url":          "https://github.com/rob531/zo-sentinel/actions/runs/123456789",
            "createdAt":    "2026-05-27T12:00:00Z",
            "displayTitle": "Some commit",
        }

    def test_row_has_canonical_mesh_memory_columns(self):
        row = ghf.build_row(self._sample_run(), "tests/test_x.py::case: assert 1==2")
        for key in ("agent_id", "memory_type", "content", "importance", "created_at"):
            self.assertIn(key, row)

    def test_row_agent_and_type(self):
        row = ghf.build_row(self._sample_run(), "")
        self.assertEqual(row["agent_id"], "gh_actions_evaluator")
        self.assertEqual(row["memory_type"], "gh_check_failure")

    def test_row_content_is_well_formed_json(self):
        row = ghf.build_row(self._sample_run(), "summary text")
        payload = json.loads(row["content"])
        self.assertEqual(payload["run_id"], 123456789)
        self.assertEqual(payload["workflow"], "evaluator")
        self.assertEqual(payload["commit_sha"], "abc1234def5678")
        self.assertEqual(payload["branch"], "feature/example")
        self.assertEqual(payload["conclusion"], "failure")
        self.assertEqual(payload["summary"], "summary text")
        self.assertFalse(payload["consumed"])
        self.assertIn("html_url", payload)

    def test_row_falls_back_to_displaytitle_when_no_summary(self):
        row = ghf.build_row(self._sample_run(), "")
        payload = json.loads(row["content"])
        self.assertEqual(payload["summary"], "Some commit")


class TestSummarizeJunit(unittest.TestCase):
    """The junit summariser is what gives the directive_architect rich
    failure context. Must extract test names + messages, must not crash on
    malformed input."""

    def test_extracts_failure(self):
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite>
              <testcase classname="tests.test_x" name="test_y">
                <failure message="assert 1 == 2">multiline
            traceback</failure>
              </testcase>
            </testsuite>
        """).strip()
        tmp = Path(self._tmp("junit_ok.xml", xml))
        out = ghf._summarize_junit(tmp)
        self.assertIn("tests.test_x::test_y", out)
        self.assertIn("assert 1 == 2", out)

    def test_extracts_error_too(self):
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite>
              <testcase classname="tests.t" name="boom">
                <error message="ImportError">stack</error>
              </testcase>
            </testsuite>
        """).strip()
        tmp = Path(self._tmp("junit_err.xml", xml))
        out = ghf._summarize_junit(tmp)
        self.assertIn("ImportError", out)

    def test_caps_at_three(self):
        cases = "\n".join(
            f'<testcase classname="c" name="n{i}"><failure message="m{i}"/></testcase>'
            for i in range(10)
        )
        xml = f"<?xml version='1.0'?><testsuite>{cases}</testsuite>"
        tmp = Path(self._tmp("junit_many.xml", xml))
        out = ghf._summarize_junit(tmp)
        # 3 failures joined by " | " — count separators, not regex on content
        self.assertLessEqual(out.count(" | "), 2)

    def test_malformed_returns_empty(self):
        tmp = Path(self._tmp("bad.xml", "<not-xml"))
        self.assertEqual(ghf._summarize_junit(tmp), "")

    def test_missing_file_returns_empty(self):
        self.assertEqual(ghf._summarize_junit(Path("/nope/does/not/exist.xml")), "")

    # --- helpers ---
    def _tmp(self, name: str, content: str) -> str:
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="zo_junit_smoke_"))
        p = d / name
        p.write_text(content, encoding="utf-8")
        return str(p)


class TestConstants(unittest.TestCase):
    """Sanity-check the public surface the directive_architect MCP and
    the workflow file both depend on."""

    def test_agent_and_memory_type_constants(self):
        self.assertEqual(ghf.AGENT_ID, "gh_actions_evaluator")
        self.assertEqual(ghf.MEMORY_TYPE, "gh_check_failure")

    def test_main_callable(self):
        # `main` exists and accepts argv; we don't actually invoke the
        # network/subprocess path here.
        self.assertTrue(callable(ghf.main))


if __name__ == "__main__":
    unittest.main()
