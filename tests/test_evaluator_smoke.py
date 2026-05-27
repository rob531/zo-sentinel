"""Smoke tests for the Goose-T2 cheap evaluator reverse-feed.

These tests cover only the pure-Python pieces of the reverse-feed loop:
row construction, junit summarisation, step extraction, and
idempotency-key parsing. They must run on any vanilla GH-hosted runner --
no /home/workspace path, no live write_service, no real `gh` CLI calls.

The fetcher's network/subprocess paths are covered separately on the
tower (manually or by the scheduled fetch-failures workflow).
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Make the repo root importable regardless of pytest invocation cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.evaluators import gh_actions_fetcher as ghf  # noqa: E402


def _sample_run() -> dict:
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


def _sample_step(job_id: int = 555, step_name: str = "Run pytest",
                 step_number: int = 4, job_name: str = "tests",
                 conclusion: str = "failure") -> dict:
    return {
        "job_id":      job_id,
        "job_name":    job_name,
        "job_url":     f"https://github.com/rob531/zo-sentinel/actions/runs/123456789/job/{job_id}",
        "step_name":   step_name,
        "step_number": step_number,
        "conclusion":  conclusion,
    }


class TestBuildRow(unittest.TestCase):
    """build_row() must produce a mesh_memory-shaped dict the write path
    accepts, with all the fields read_failure_history expects -- now at
    per-step granularity."""

    def test_row_has_canonical_mesh_memory_columns(self):
        row = ghf.build_row(_sample_run(), _sample_step(), "tests/test_x.py::case: assert 1==2")
        for key in ("agent_id", "memory_type", "content", "importance", "created_at"):
            self.assertIn(key, row)

    def test_row_agent_and_type(self):
        row = ghf.build_row(_sample_run(), _sample_step(), "")
        self.assertEqual(row["agent_id"], "gh_actions_evaluator")
        self.assertEqual(row["memory_type"], "gh_check_failure")

    def test_row_content_is_well_formed_json(self):
        row = ghf.build_row(_sample_run(), _sample_step(), "summary text")
        payload = json.loads(row["content"])
        # New per-step dedupe key fields
        self.assertEqual(payload["workflow_run_id"], 123456789)
        self.assertEqual(payload["job_id"], 555)
        self.assertEqual(payload["step_name"], "Run pytest")
        self.assertEqual(payload["step_number"], 4)
        self.assertEqual(payload["job_name"], "tests")
        # Existing run-level fields
        self.assertEqual(payload["workflow"], "evaluator")
        self.assertEqual(payload["commit_sha"], "abc1234def5678")
        self.assertEqual(payload["branch"], "feature/example")
        self.assertEqual(payload["conclusion"], "failure")
        self.assertEqual(payload["summary"], "summary text")
        self.assertFalse(payload["consumed"])
        self.assertIn("html_url", payload)

    def test_row_html_url_includes_step_anchor(self):
        row = ghf.build_row(_sample_run(), _sample_step(step_number=7), "x")
        payload = json.loads(row["content"])
        self.assertIn("#step:7", payload["html_url"])

    def test_row_falls_back_summary_when_no_junit(self):
        """Step-level fallback summary should mention job name, step name,
        conclusion, and link -- NOT the run-level displayTitle."""
        row = ghf.build_row(_sample_run(), _sample_step(job_name="lint",
                                                       step_name="Run ruff",
                                                       conclusion="failure"),
                            "")
        payload = json.loads(row["content"])
        self.assertIn("lint", payload["summary"])
        self.assertIn("Run ruff", payload["summary"])
        self.assertIn("failure", payload["summary"])


class TestExtractFailedSteps(unittest.TestCase):
    """_extract_failed_steps walks the gh `run view --json jobs` payload
    and returns one entry per failed step within each failed job."""

    def test_returns_each_failed_step(self):
        payload = {
            "jobs": [
                {
                    "databaseId": 100,
                    "name": "tests",
                    "conclusion": "failure",
                    "url": "https://gh/.../job/100",
                    "steps": [
                        {"name": "Set up Python", "number": 1, "conclusion": "success"},
                        {"name": "Run pytest",    "number": 2, "conclusion": "failure"},
                        {"name": "Upload junit",  "number": 3, "conclusion": "failure"},
                    ],
                },
                {
                    "databaseId": 200,
                    "name": "lint",
                    "conclusion": "success",
                    "steps": [
                        {"name": "ruff", "number": 1, "conclusion": "success"},
                    ],
                },
            ],
        }
        steps = ghf._extract_failed_steps(payload)
        self.assertEqual(len(steps), 2)
        names = {s["step_name"] for s in steps}
        self.assertEqual(names, {"Run pytest", "Upload junit"})
        for s in steps:
            self.assertEqual(s["job_id"], 100)
            self.assertEqual(s["job_name"], "tests")

    def test_skips_jobs_that_did_not_fail(self):
        payload = {
            "jobs": [
                {
                    "databaseId": 1,
                    "name": "build",
                    "conclusion": "success",
                    "steps": [
                        {"name": "weird", "number": 1, "conclusion": "failure"},
                    ],
                }
            ]
        }
        # The job overall succeeded -- we don't surface its failed step.
        self.assertEqual(ghf._extract_failed_steps(payload), [])

    def test_empty_payload_safe(self):
        self.assertEqual(ghf._extract_failed_steps({}), [])
        self.assertEqual(ghf._extract_failed_steps({"jobs": []}), [])


class TestPerStepDedupe(unittest.TestCase):
    """End-to-end-ish test of the (run_id, job_id, step_name) dedupe logic
    by patching out gh + write paths and observing which rows
    `fetch_and_write` actually emits."""

    def _patch_gh_layer(self, runs, steps_by_run):
        """Returns a context-manager-style stack of mocks for gh + write
        primitives. `runs` is the list_recent_runs return; `steps_by_run`
        is a dict run_id -> list of failed-step dicts."""
        patches = [
            mock.patch.object(ghf, "_gh_available", return_value=True),
            mock.patch.object(ghf, "_gh_auth_ok",   return_value=True),
            mock.patch.object(ghf, "list_recent_runs", return_value=runs),
            mock.patch.object(ghf, "list_failed_steps",
                              side_effect=lambda repo, rid: steps_by_run.get(rid, [])),
            mock.patch.object(ghf, "fetch_junit_summary", return_value=""),
            mock.patch.object(ghf, "_sqlite_has_key", return_value=False),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_same_triple_twice_is_deduped(self):
        runs = [_sample_run()]
        steps = {123456789: [_sample_step()]}
        self._patch_gh_layer(runs, steps)

        written: list[dict] = []
        seen_keys: set[tuple[int, int, str]] = set()

        def fake_existing_keys():
            return set(seen_keys)

        def fake_write(row):
            payload = json.loads(row["content"])
            seen_keys.add(ghf._key_triple(payload))
            written.append(payload)
            return True

        with mock.patch.object(ghf, "existing_keys", side_effect=fake_existing_keys), \
             mock.patch.object(ghf, "write_mesh_row", side_effect=fake_write):
            r1 = ghf.fetch_and_write("rob531/zo-sentinel", 5)
            r2 = ghf.fetch_and_write("rob531/zo-sentinel", 5)

        self.assertEqual(r1["written"], 1)
        self.assertEqual(r2["written"], 0)
        self.assertEqual(r2["skipped"], 1)
        self.assertEqual(len(written), 1)

    def test_same_run_different_job_both_inserted(self):
        runs = [_sample_run()]
        steps = {
            123456789: [
                _sample_step(job_id=100, job_name="tests",
                             step_name="Run pytest", step_number=2),
                _sample_step(job_id=200, job_name="lint",
                             step_name="Run pytest", step_number=2),
            ]
        }
        self._patch_gh_layer(runs, steps)

        written: list[dict] = []

        def fake_write(row):
            written.append(json.loads(row["content"]))
            return True

        with mock.patch.object(ghf, "existing_keys", return_value=set()), \
             mock.patch.object(ghf, "write_mesh_row", side_effect=fake_write):
            res = ghf.fetch_and_write("rob531/zo-sentinel", 5)

        self.assertEqual(res["written"], 2)
        job_ids = {p["job_id"] for p in written}
        self.assertEqual(job_ids, {100, 200})

    def test_same_run_same_job_different_step_both_inserted(self):
        runs = [_sample_run()]
        steps = {
            123456789: [
                _sample_step(job_id=100, step_name="Run pytest", step_number=2),
                _sample_step(job_id=100, step_name="Upload junit", step_number=3),
            ]
        }
        self._patch_gh_layer(runs, steps)

        written: list[dict] = []

        def fake_write(row):
            written.append(json.loads(row["content"]))
            return True

        with mock.patch.object(ghf, "existing_keys", return_value=set()), \
             mock.patch.object(ghf, "write_mesh_row", side_effect=fake_write):
            res = ghf.fetch_and_write("rob531/zo-sentinel", 5)

        self.assertEqual(res["written"], 2)
        step_names = {p["step_name"] for p in written}
        self.assertEqual(step_names, {"Run pytest", "Upload junit"})


class TestKeyTriple(unittest.TestCase):
    """_key_triple must extract (run_id, job_id, step_name) defensively
    so legacy run_id-only rows don't crash the set lookup."""

    def test_new_schema(self):
        self.assertEqual(
            ghf._key_triple({
                "workflow_run_id": 1, "job_id": 2, "step_name": "x",
            }),
            (1, 2, "x"),
        )

    def test_legacy_run_id_only(self):
        # Legacy rows lack workflow_run_id/job_id/step_name -- we tolerate
        # them by mapping to a sentinel that won't collide with new rows.
        self.assertEqual(
            ghf._key_triple({"run_id": 42}),
            (42, 0, ""),
        )

    def test_garbage_values_dont_crash(self):
        self.assertEqual(
            ghf._key_triple({
                "workflow_run_id": "not-int", "job_id": None, "step_name": None,
            }),
            (0, 0, ""),
        )


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
        # 3 failures joined by " | " -- count separators, not regex on content
        self.assertLessEqual(out.count(" | "), 2)

    def test_malformed_returns_empty(self):
        tmp = Path(self._tmp("bad.xml", "<not-xml"))
        self.assertEqual(ghf._summarize_junit(tmp), "")

    def test_missing_file_returns_empty(self):
        self.assertEqual(ghf._summarize_junit(Path("/nope/does/not/exist.xml")), "")

    # --- helpers ---
    def _tmp(self, name: str, content: str) -> str:
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
