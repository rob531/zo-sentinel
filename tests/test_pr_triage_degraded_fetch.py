"""The PR fetch is the whole run, so it gets tested like a gate.

Scar being defended against (FU-129): triage fetched `files` +
`statusCheckRollup` for up to 300 PRs in ONE GraphQL query. Both are per-PR
connections, so the cost scales with the backlog the tool exists to clear --
and on 2026-07-28T03:33Z GitHub answered HTTP 504 on five consecutive attempts
(71s, the existing exponential backoff fully spent). One call carried the whole
run, so five 504s meant ZERO PRs triaged: a silent total gate outage reported
as a red `triage` check sitting on innocent PRs.

The fix is not more retries -- a query that is over budget is over budget every
time. It is to DEGRADE: cheap metadata list + per-PR hydration.

What these tests pin:
  1. the fast path is still one query and is preferred;
  2. a transient failure degrades instead of failing the run;
  3. a PR that cannot be hydrated is DROPPED, never classified on missing data
     (files drives the dup/scaffold buckets -- a wrong label beats no label
     nowhere);
  4. rate-limit stays a distinct GATE OUTAGE, not a 504;
  5. a non-transient error still fails loudly rather than degrading;
  6. "everything dropped" does NOT masquerade as "nothing to do" (exit 0).
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

FULL_ROW = {
    "number": 1, "title": "build: x", "labels": [], "mergeable": "MERGEABLE",
    "files": FILES, "statusCheckRollup": ROLLUP,
}
CHEAP_ROW = {"number": 1, "title": "build: x", "labels": [], "mergeable": "MERGEABLE"}

T504 = "ERROR: HTTP 504: We couldn't respond to your request in time. (graphql)"

# The SAME over-budget query, MEASURED failing three different ways in four
# consecutive live attempts on 2026-07-28. The stream-cancel shape matched none
# of the original _TRANSIENT_GH tokens, so the degrade path would not have
# armed on it -- a fallback that misses a third of the real failures is not a
# fallback. Every observed shape is pinned here.
OBSERVED_FAILURES = (
    "ERROR: HTTP 504: 504 Gateway Timeout (https://api.github.com/graphql)",
    "ERROR: HTTP 504: We couldn't respond to your request in time. Sorry about "
    "that. Please try resubmitting your request and contact us if the problem "
    "persists. (https://api.github.com/graphql)",
    "ERROR: gh pr list failed: stream error: stream ID 1; CANCEL; received from peer",
)


def _cp(rc, out="", err=""):
    return subprocess.CompletedProcess(["gh"], rc, out, err)


class _Router:
    """Stand-in for _gh that answers by command shape and records the calls."""

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


def _install(monkey_target, router):
    setattr(pr_triage, "_gh", router)


class FastPath(unittest.TestCase):
    def setUp(self):
        self._orig = pr_triage._gh

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_single_query_when_it_works(self):
        r = _Router(full=_cp(0, json.dumps([FULL_ROW])))
        _install(pr_triage, r)
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "full")
        self.assertEqual(dropped, [])
        self.assertEqual(prs[0]["files"], FILES)
        # The whole point of the fast path: it costs exactly one call.
        self.assertEqual(len(r.calls), 1)

    def test_non_transient_error_does_not_degrade(self):
        # A real error must fail loudly. Degrading on everything would turn a
        # broken token into a quiet half-run.
        r = _Router(full=_cp(1, "", "ERROR: could not resolve to a Repository"))
        _install(pr_triage, r)
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "error")
        self.assertEqual(prs, [])
        self.assertEqual(len(r.calls), 1)  # no fallback attempted

    def test_rate_limit_is_its_own_animal(self):
        r = _Router(full=_cp(1, "", "ERROR: API rate limit exceeded"))
        _install(pr_triage, r)
        _, mode, _ = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "rate_limited")


class DegradedPath(unittest.TestCase):
    def setUp(self):
        self._orig = pr_triage._gh

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_504_degrades_and_still_returns_full_shape(self):
        r = _Router(
            full=_cp(1, "", T504),
            cheap=_cp(0, json.dumps([CHEAP_ROW])),
            view=_cp(0, json.dumps({"files": FILES, "statusCheckRollup": ROLLUP})),
        )
        _install(pr_triage, r)
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "degraded")
        self.assertEqual(dropped, [])
        self.assertEqual(len(prs), 1)
        # Hydrated rows must be indistinguishable from fast-path rows, or the
        # classifier silently behaves differently in degraded mode.
        self.assertEqual(prs[0]["files"], FILES)
        self.assertEqual(prs[0]["statusCheckRollup"], ROLLUP)
        self.assertEqual(prs[0]["title"], "build: x")

    def test_unhydratable_pr_is_dropped_not_guessed(self):
        r = _Router(
            full=_cp(1, "", T504),
            cheap=_cp(0, json.dumps([CHEAP_ROW])),
            view=_cp(1, "", "ERROR: 504 again"),
        )
        _install(pr_triage, r)
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "degraded")
        self.assertEqual(prs, [])
        self.assertEqual(dropped, [1])

    def test_partial_hydration_response_is_also_dropped(self):
        # gh answering 0 with only half the fields is the subtle case: the row
        # would classify, just wrongly.
        r = _Router(
            full=_cp(1, "", T504),
            cheap=_cp(0, json.dumps([CHEAP_ROW])),
            view=_cp(0, json.dumps({"files": FILES})),
        )
        _install(pr_triage, r)
        prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(prs, [])
        self.assertEqual(dropped, [1])

    def test_degraded_path_failing_too_is_an_error(self):
        r = _Router(full=_cp(1, "", T504), cheap=_cp(1, "", T504))
        _install(pr_triage, r)
        _, mode, _ = pr_triage.fetch_open_build_prs("o/r")
        self.assertEqual(mode, "error")


class DroppedIsNotEmpty(unittest.TestCase):
    """`prs == []` means two different things and main() must not conflate them."""

    def setUp(self):
        self._orig_fetch = pr_triage.fetch_open_build_prs
        self._orig_repo = pr_triage._repo
        pr_triage._repo = lambda: "o/r"

    def tearDown(self):
        pr_triage.fetch_open_build_prs = self._orig_fetch
        pr_triage._repo = self._orig_repo

    def test_genuinely_no_prs_is_success(self):
        pr_triage.fetch_open_build_prs = lambda repo: ([], "full", [])
        self.assertEqual(pr_triage.main(), 0)

    def test_all_dropped_is_an_outage_not_a_clean_run(self):
        pr_triage.fetch_open_build_prs = lambda repo: ([], "degraded", [1, 2, 3])
        self.assertEqual(pr_triage.main(), 1)

    def test_rate_limited_is_an_outage(self):
        pr_triage.fetch_open_build_prs = lambda repo: ([], "rate_limited", [])
        self.assertEqual(pr_triage.main(), 1)


class ClassifierUnchangedByMode(unittest.TestCase):
    """Degraded rows must reach the same verdict as fast-path rows."""

    def test_same_verdict_either_way(self):
        fast = pr_triage.classify([dict(FULL_ROW)], repo="", exempted=set())
        merged = dict(CHEAP_ROW)
        merged.update({"files": FILES, "statusCheckRollup": ROLLUP})
        slow = pr_triage.classify([merged], repo="", exempted=set())
        self.assertEqual(fast, slow)



class ObservedFailureShapes(unittest.TestCase):
    """Every failure shape the live API actually produced must degrade."""

    def setUp(self):
        self._orig = pr_triage._gh

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_all_observed_shapes_are_transient(self):
        for err in OBSERVED_FAILURES:
            self.assertTrue(pr_triage._is_transient(err), err[:60])

    def test_all_observed_shapes_reach_the_degraded_path(self):
        for err in OBSERVED_FAILURES:
            r = _Router(
                full=_cp(1, "", err),
                cheap=_cp(0, json.dumps([CHEAP_ROW])),
                view=_cp(0, json.dumps({"files": FILES,
                                        "statusCheckRollup": ROLLUP})),
            )
            _install(pr_triage, r)
            prs, mode, dropped = pr_triage.fetch_open_build_prs("o/r")
            self.assertEqual(mode, "degraded", err[:60])
            self.assertEqual(len(prs), 1, err[:60])

    def test_still_not_a_catch_all(self):
        # The widened list must not swallow real errors.
        for err in ("ERROR: could not resolve to a Repository",
                    "ERROR: GraphQL: Resource not accessible by integration",
                    "ERROR: unknown flag: --nope"):
            self.assertFalse(pr_triage._is_transient(err), err)


if __name__ == "__main__":
    unittest.main()
