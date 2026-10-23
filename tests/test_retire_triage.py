#!/usr/bin/env python3
"""Both poles of tools/retire_triage.py --check-doc, observed, not asserted.

R4: an assertion never seen RED is an untested branch. The latch in
retire_triage.py exists to go RED when a markdown table asserts RETIRE for a
module whose premise does not hold live. So this file makes it do both:

  NEGATIVE CONTROL  a table asserting RETIRE for a module that is HOLD live
                    -> rc=1
  POSITIVE CONTROL  a table asserting RETIRE only for modules that are RETIRE
                    live -> rc=0
  R6 CONTROL        an unreadable table -> rc=2, never rc=0

`audit_log_api` is the negative-control subject on purpose: it is the module
docs/G4_REACHABILITY_ANALYSIS.md nominated for deletion as "declares no routes"
while it served two (chairman review 2026-09-21, counter fixed in PR #5375).
If this test ever goes green on it, the counter has regressed.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "retire_triage.py")

HEADER = "| # | module | verdict | why |\n|---|---|---|---|\n"


def _run(*args):
    p = subprocess.run([sys.executable, TOOL] + list(args),
                       capture_output=True, text=True, timeout=600, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def _doc(rows):
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(HEADER + "".join(
            "| %d | `%s` | **RETIRE** | fixture |\n" % (i + 1, s)
            for i, s in enumerate(rows)))
    return path


class CheckDocPoles(unittest.TestCase):

    def test_negative_control_unsupported_retire_goes_red(self):
        path = _doc(["audit_log_api"])
        try:
            rc, out = _run("--check-doc", path)
        finally:
            os.unlink(path)
        self.assertEqual(rc, 1, out)
        self.assertIn("UNSUPPORTED", out)
        self.assertIn("audit_log_api", out)

    def test_positive_control_supported_retire_is_green(self):
        """risk_tier_threshold_api is the one deletion the 2026-09-21 chairman
        review recommended: zero routes, zero importers, no staged, no active."""
        path = _doc(["risk_tier_threshold_api"])
        try:
            rc, out = _run("--check-doc", path)
        finally:
            os.unlink(path)
        self.assertEqual(rc, 0, out)
        self.assertIn("holds live", out)

    def test_unreadable_doc_is_rc2_not_a_pass(self):
        rc, out = _run("--check-doc",
                       os.path.join(tempfile.gettempdir(), "no_such_table_c165.md"))
        self.assertEqual(rc, 2, out)

    def test_no_class_wide_override_clears_the_eleven(self):
        """The #4004 override cleared 11 modules because a services/staged/<stem>/
        existed. Every one of the 8 still deferred must be HOLD."""
        path = _doc(["api_key_manager", "audit_log_api",
                     "deferred_router_ledger_report", "deferred_router_triage_report",
                     "registry_ingest_anomaly_report", "risk_tier_trend_api",
                     "server_risk_detail_api", "server_submission_api"])
        try:
            rc, out = _run("--check-doc", path)
        finally:
            os.unlink(path)
        self.assertEqual(rc, 1, out)
        self.assertIn("8 of 8", out)

    def test_retire_is_never_emitted_for_a_module_with_live_routes(self):
        rc, out = _run("--only", "audit_log_api")
        self.assertEqual(rc, 0, out)
        self.assertIn("[HOLD", out)
        self.assertNotIn("[RETIRE", out)


if __name__ == "__main__":
    unittest.main()
