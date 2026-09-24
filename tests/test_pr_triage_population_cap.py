"""A --limit is a population cap wearing a count's clothes (FU-446).

Scar: `fetch_open_build_prs` asked for `--limit 300` and returned whatever came
back, as if it were the population. Measured live on 2026-09-12 against
rob531/zo-sentinel the open autonomous-build queue was 296 -- FOUR rows below
the cap -- while auto-merge.yml's own comment recorded the queue growing
276 -> 321 in two days. The failure mode on crossing is SILENT: gh exits 0 with
exactly 300 rows, every one of them is triaged correctly, and the PRs past the
page boundary are never seen. Nothing goes red.

The same shape was already live in two other places in this repo the same day:

  * auto-merge.yml `triage-solid-sweep` passed NO --limit at all, so gh applied
    its default of 30. Measured: 30 rows fetched, 132 PRs actually carrying both
    labels, 19 unarmed inside the page, 104 unarmed in the population -- 85
    structurally unreachable. Its own comment claimed "this job is UNCAPPED".
  * queue_census.py asked for `--limit 300` while 314 PRs were open, so the tool
    whose entire job is reporting queue DEPTH under-reported it by 14.

And pr-relander.yml already carried the warning ("--limit is MANDATORY. gh pr
list defaults to 30 rows") from an earlier incident. The lesson was written down
in one file and never censused across the others, which is why these tests pin
the BEHAVIOUR rather than trusting a comment.

What these tests pin:
  1. a full-query result exactly at the cap DEGRADES instead of being returned
     as a complete answer  <- the negative control; this fails without the fix;
  2. a result below the cap still takes the one-query fast path;
  3. the degraded path asks for the larger limit, so degrading actually widens
     the window rather than re-capping at the same number;
  4. `_capped` discriminates -- True at the boundary, False below it. A detector
     that never returns False would be a rubber stamp.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pr_triage  # noqa: E402

FILES = [{"path": "services/staged/x/router.py", "additions": 40}]
ROLLUP = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]


def _row(n):
    return {"number": n, "title": f"build: x{n}", "labels": [],
            "mergeable": "MERGEABLE", "files": FILES, "statusCheckRollup": ROLLUP}


def _cheap_row(n):
    return {"number": n, "title": f"build: x{n}", "labels": [],
            "mergeable": "MERGEABLE"}


def _cp(rc, out="", err=""):
    return subprocess.CompletedProcess(["gh"], rc, out, err)


class _Router:
    """Stand-in for _gh that answers by command shape and records the argv."""

    def __init__(self, full=None, cheap=None, view=None):
        self.full, self.cheap, self.view = full, cheap, view
        self.calls = []

    def __call__(self, *args, **kw):
        self.calls.append(args)
        joined = " ".join(args)
        if args[:2] == ("pr", "view"):
            return self.view(args) if callable(self.view) else self.view
        if "statusCheckRollup" in joined:
            return self.full
        return self.cheap

    def limit_for(self, needle):
        """The --limit gh was actually asked for, on the call matching needle."""
        for args in self.calls:
            joined = " ".join(args)
            if needle in joined:
                return args[args.index("--limit") + 1]
        return None


class CapDetector(unittest.TestCase):
    """A detector that cannot return False is not a detector."""

    def test_true_at_the_boundary(self):
        self.assertTrue(pr_triage._capped([0] * 300, "probe", 300))

    def test_false_below_the_boundary(self):
        self.assertFalse(pr_triage._capped([0] * 299, "probe", 300))

    def test_true_above_the_boundary(self):
        # gh should never exceed its own limit, but a detector that only fires
        # on exact equality would miss a paginating client that overshoots.
        self.assertTrue(pr_triage._capped([0] * 301, "probe", 300))


class CapHitDegrades(unittest.TestCase):
    def setUp(self):
        self._orig = pr_triage._gh

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_full_result_at_the_cap_is_not_reported_as_complete(self):
        """THE NEGATIVE CONTROL. Without the fix this returns mode='full'."""
        capped = [_row(i) for i in range(pr_triage._FULL_LIMIT)]
        r = _Router(full=_cp(0, json.dumps(capped)),
                    cheap=_cp(0, json.dumps([_cheap_row(1)])),
                    view=_cp(0, json.dumps({"files": FILES,
                                            "statusCheckRollup": ROLLUP})))
        pr_triage._gh = r
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "degraded",
                         "a result exactly as long as its own page size is a "
                         "CAP, not a count, and must not be served as the "
                         "whole population")
        self.assertGreater(len(r.calls), 1, "it must actually re-query")

    def test_below_the_cap_still_takes_the_one_query_fast_path(self):
        """The other pole: the fix must not turn every run into a degrade."""
        under = [_row(i) for i in range(pr_triage._FULL_LIMIT - 1)]
        r = _Router(full=_cp(0, json.dumps(under)))
        pr_triage._gh = r
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "full")
        self.assertEqual(dropped, [])
        self.assertEqual(len(prs), pr_triage._FULL_LIMIT - 1)
        self.assertEqual(len(r.calls), 1, "fast path still costs exactly one call")

    def test_degrading_widens_the_window_rather_than_re_capping(self):
        """Degrading to the SAME limit would trade one blind spot for itself."""
        capped = [_row(i) for i in range(pr_triage._FULL_LIMIT)]
        r = _Router(full=_cp(0, json.dumps(capped)),
                    cheap=_cp(0, json.dumps([_cheap_row(1)])),
                    view=_cp(0, json.dumps({"files": FILES,
                                            "statusCheckRollup": ROLLUP})))
        pr_triage._gh = r
        pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(r.limit_for("statusCheckRollup"),
                         str(pr_triage._FULL_LIMIT))
        self.assertEqual(r.limit_for("number,title,labels,mergeable"),
                         str(pr_triage._CHEAP_LIMIT))
        self.assertGreater(pr_triage._CHEAP_LIMIT, pr_triage._FULL_LIMIT)

    def test_transient_failure_still_degrades(self):
        """The pre-existing 504 path (FU-129) must be untouched by this change."""
        r = _Router(full=_cp(1, "", "ERROR: HTTP 504: 504 Gateway Timeout"),
                    cheap=_cp(0, json.dumps([_cheap_row(1)])),
                    view=_cp(0, json.dumps({"files": FILES,
                                            "statusCheckRollup": ROLLUP})))
        pr_triage._gh = r
        _, mode, _ = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "degraded")

    def test_non_transient_error_still_fails_loudly(self):
        r = _Router(full=_cp(1, "", "ERROR: could not resolve to a Repository"))
        pr_triage._gh = r
        prs, mode, _ = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "error")
        self.assertEqual(len(r.calls), 1, "no fallback on a real error")

    def test_rate_limit_is_still_its_own_animal(self):
        r = _Router(full=_cp(1, "", "ERROR: API rate limit exceeded"))
        pr_triage._gh = r
        _, mode, _ = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "rate_limited")


class WorkflowCallSitesAreCensused(unittest.TestCase):
    """One door of eight is not a cure -- pin every `gh pr list` in CI."""

    WF = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")

    def _lines(self, name):
        """Logical lines: shell continuations are joined before matching.

        `--limit 500` sitting on the next physical line after a trailing `\\` is
        present, not absent. A censusing test that cannot read a continuation
        reports offenders that are already fixed -- which is how a census loses
        the trust that makes it worth running.
        """
        with open(os.path.join(self.WF, name), encoding="utf-8") as fh:
            raw = fh.read()
        joined, out = raw.replace("\\\n", " "), []
        for ln in joined.splitlines():
            if "gh pr list" in ln and not ln.lstrip().startswith("#"):
                out.append(ln)
        return out

    def test_every_workflow_pr_list_carries_an_explicit_limit(self):
        offenders = []
        for name in os.listdir(self.WF):
            if not name.endswith((".yml", ".yaml")):
                continue
            for ln in self._lines(name):
                if "--limit" not in ln and "--head" not in ln:
                    offenders.append(f"{name}: {ln.strip()[:100]}")
        self.assertEqual(offenders, [],
                         "gh pr list defaults to 30 rows. A sweep without an "
                         "explicit --limit is blind past row 30 and says "
                         "nothing about it. (--head queries are exempt: they "
                         "look up one branch.)")


if __name__ == "__main__":
    unittest.main()
