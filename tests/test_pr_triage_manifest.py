"""The service-manifest exemption is a GATE, so it gets tested like one.

Scar being defended against: `MIN_SOLID_ADDITIONS = 12` silently condemned every
`services/<stage>/<name>/service.toml` as a stub, so the file that
`promote_staged_to_active.py` blocks on (gate 1: name + import_path) and that
`generate_spine.py` treats as registration never landed -- 50 staged services on
main, only 13 with a manifest. The exemption must be narrow AND content-checked;
a bare path match would just be a new hole.
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pr_triage  # noqa: E402

GOOD = (
    "+[service]\n"
    '+name = "risk_summary"\n'
    '+import_path = "services.staged.risk_summary.router"\n'
    '+prefix = "/api/risk"\n'
)
NO_IMPORT_PATH = '+[service]\n+name = "risk_summary"\n'
BROKEN_TOML = '+[service\n+name = "risk_summary"\n'


def _fake_gh(stdout, rc=0):
    def _gh(*args, **kw):
        return subprocess.CompletedProcess(list(args), rc, stdout, "")
    return _gh


class ManifestRecognition(unittest.TestCase):
    def test_matches_staged_and_active_manifests(self):
        for p in ("services/staged/risk_summary/service.toml",
                  "services/active/vuln_exposure_api/service.toml"):
            self.assertTrue(pr_triage.MANIFEST_RE.match(p), p)

    def test_rejects_lookalikes(self):
        for p in ("app/service.toml", "services/staged/service.toml",
                  "services/staged/a/b/service.toml",
                  "services/staged/x/service.toml.bak"):
            self.assertFalse(pr_triage.MANIFEST_RE.match(p), p)

    def test_mixed_pr_is_not_manifest_only(self):
        self.assertFalse(pr_triage._manifest_only([
            {"path": "services/staged/x/service.toml"},
            {"path": "app/main.py"},
        ]))

    def test_single_manifest_is_manifest_only(self):
        self.assertTrue(pr_triage._manifest_only(
            [{"path": "services/staged/x/service.toml", "additions": 7}]))


class ManifestValidation(unittest.TestCase):
    """Validate CONTENT, not the path -- fail closed on anything unreadable."""

    def _valid(self, stdout, rc=0):
        real, pr_triage._gh = pr_triage._gh, _fake_gh(stdout, rc)
        try:
            return pr_triage._manifest_is_valid("o/r", 1)
        finally:
            pr_triage._gh = real

    def test_accepts_manifest_with_required_keys(self):
        self.assertTrue(self._valid(GOOD))

    def test_rejects_missing_import_path(self):
        self.assertFalse(self._valid(NO_IMPORT_PATH))

    def test_rejects_unparseable_toml(self):
        self.assertFalse(self._valid(BROKEN_TOML))

    def test_rejects_empty_diff(self):
        self.assertFalse(self._valid(""))

    def test_fails_closed_when_gh_fails(self):
        self.assertFalse(self._valid(GOOD, rc=1))


class ClassifyIntegration(unittest.TestCase):
    GREEN = [{"conclusion": "SUCCESS", "status": "COMPLETED"}]

    def _pr(self, n, path, additions):
        return {"number": n, "title": "build: scaffold_x_service_toml",
                "files": [{"path": path, "additions": additions}],
                "mergeable": "MERGEABLE", "statusCheckRollup": self.GREEN}

    def _classify(self, prs, stdout=GOOD):
        real, pr_triage._gh = pr_triage._gh, _fake_gh(stdout)
        exempted: set = set()
        try:
            return pr_triage.classify(prs, "o/r", exempted), exempted
        finally:
            pr_triage._gh = real

    def test_valid_manifest_becomes_solid_and_is_recorded(self):
        out, exempted = self._classify(
            [self._pr(1, "services/staged/risk_summary/service.toml", 7)])
        self.assertEqual(out[1], "solid")
        self.assertEqual(exempted, {1})

    def test_invalid_manifest_stays_scaffold(self):
        out, exempted = self._classify(
            [self._pr(1, "services/staged/risk_summary/service.toml", 7)],
            stdout=NO_IMPORT_PATH)
        self.assertEqual(out[1], "scaffold")
        self.assertEqual(exempted, set())

    def test_tiny_python_stub_is_still_a_scaffold(self):
        out, exempted = self._classify([self._pr(1, "app/tiny_stub.py", 3)])
        self.assertEqual(out[1], "scaffold")
        self.assertEqual(exempted, set())

    def test_exemption_never_arms_without_a_repo(self):
        prs = [self._pr(1, "services/staged/risk_summary/service.toml", 7)]
        self.assertEqual(pr_triage.classify(prs)[1], "scaffold")


if __name__ == "__main__":
    unittest.main()
