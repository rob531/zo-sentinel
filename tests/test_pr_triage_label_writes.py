"""The label WRITES are a budget, and the budget is the gate (FU-136).

Scar being defended against: #2172 repaired the FETCH, and the very next
scheduled runs -- 2026-07-28T06:11Z and 06:23Z -- ran for 8m16s and 8m09s and
were then KILLED by the job's `timeout-minutes: 8`. GitHub reports a
timeout-killed job as `cancelled`, not `failure`, and because stdout was block
buffered the runs emitted ZERO lines. Two total gate outages that looked like
neither a failure nor an outage.

MEASURED on the live API 2026-07-28T07:52Z, which is what settled it:
    apply_label = 4 sequential `gh pr edit` calls   -> 4.06s per PR
    119 open autonomous-build PRs                   -> 483s
    ...over the 8-minute job budget ON ITS OWN, before the ~136s the fetch
    already spends (71s of doomed combined query + 65s of hydration).

The cost was never NEW. It was never PAID, because every previous run died in
the fetch first. Fixing the first blocker exposed the second, and #2172's
"76.1s live proof" had timed only the read half -- the half that had ever run.

What these tests pin:
  1. an already-correct PR costs ZERO gh calls (the steady state, and the
     entire fix -- the sweep is idempotent, so it re-asserts the same label
     forever);
  2. a genuine relabel costs exactly ONE call, add and removes combined;
  3. we NEVER ask to remove a label we have not SEEN -- that is the invariant
     that makes combining add+remove safe at all;
  4. unknown labels fall back to the ORIGINAL separate-call shape rather than
     guessing;
  5. at 119 steady-state PRs the whole label phase costs 0 calls, not 476;
  6. when the budget IS spent, the run still writes a COMPLETE digest and exits
     0 -- a partial label pass beats a killed job that writes nothing;
  7. the doomed combined query is not retried four times into an 8-minute
     budget.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pr_triage  # noqa: E402

REPO = "o/r"


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["gh"], rc, out, err)


class _Spy:
    """Records every _gh invocation so cost is asserted in CALLS, not seconds."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else _cp(0)

    def __call__(self, *args, **kw):
        self.calls.append((args, kw))
        return self.result


def _row(number, labels, **extra):
    r = {"number": number, "title": f"build: t{number}", "mergeable": "MERGEABLE",
         "labels": [{"name": x} for x in labels]}
    r.update(extra)
    return r


class LabelCost(unittest.TestCase):
    def setUp(self):
        self._orig = pr_triage._gh
        self.spy = _Spy()
        pr_triage._gh = self.spy

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_already_correct_costs_nothing(self):
        n = pr_triage.apply_label(REPO, 7, "stale", {"triage:stale"})
        self.assertEqual(n, 0)
        self.assertEqual(self.spy.calls, [], "a converged PR must cost ZERO calls")

    def test_relabel_is_exactly_one_call(self):
        n = pr_triage.apply_label(REPO, 7, "solid", {"triage:stale"})
        self.assertEqual(n, 1)
        self.assertEqual(len(self.spy.calls), 1)
        args = self.spy.calls[0][0]
        self.assertIn("--add-label", args)
        self.assertIn("triage:solid", args)
        self.assertIn("--remove-label", args)
        self.assertIn("triage:stale", args)

    def test_never_removes_a_label_it_has_not_seen(self):
        # THE safety invariant. `gh pr edit --remove-label` errors on a label the
        # PR does not carry, and in a combined call that error takes the ADD down
        # with it. Blind removes are only survivable when they are their own call.
        pr_triage.apply_label(REPO, 7, "solid", {"triage:stale"})
        args = self.spy.calls[0][0]
        self.assertNotIn("triage:dup", args)
        self.assertNotIn("triage:scaffold", args)

    def test_first_label_on_a_bare_pr_is_one_add_and_no_removes(self):
        n = pr_triage.apply_label(REPO, 7, "dup", set())
        self.assertEqual(n, 1)
        args = self.spy.calls[0][0]
        self.assertIn("triage:dup", args)
        self.assertNotIn("--remove-label", args)

    def test_multiple_stale_labels_still_one_call(self):
        n = pr_triage.apply_label(REPO, 7, "solid", {"triage:stale", "triage:dup"})
        self.assertEqual(n, 1)
        args = self.spy.calls[0][0]
        self.assertEqual(args.count("--remove-label"), 2)

    def test_unknown_labels_keeps_the_original_separate_call_shape(self):
        # Fail-open on COST, never on correctness: with no knowledge of the
        # current labels the old tolerant shape is the only safe one.
        n = pr_triage.apply_label(REPO, 7, "solid", None)
        self.assertEqual(n, 4)
        self.assertEqual(len(self.spy.calls), 4)
        self.assertNotIn("--remove-label", self.spy.calls[0][0])

    def test_steady_state_119_prs_costs_zero_calls(self):
        # The actual regression, at the actual scale that broke it: 119 PRs
        # already carrying the right label used to cost 476 gh calls / 483s.
        total = 0
        for i in range(119):
            total += pr_triage.apply_label(REPO, i, "stale", {"triage:stale"})
        self.assertEqual(total, 0)
        self.assertEqual(len(self.spy.calls), 0)


class LabelsOf(unittest.TestCase):
    def test_reads_triage_labels_from_the_row(self):
        got = pr_triage._triage_labels_of(_row(1, ["autonomous-build", "triage:stale"]))
        self.assertEqual(got, {"triage:stale"})

    def test_missing_labels_key_is_unknown_not_empty(self):
        # None and set() must NOT be conflated: one means "nothing to remove",
        # the other means "we have no idea what is on this PR".
        self.assertIsNone(pr_triage._triage_labels_of({"number": 1}))
        self.assertEqual(pr_triage._triage_labels_of({"number": 1, "labels": []}), set())


class Deadline(unittest.TestCase):
    """A tool that stops itself writes a digest; a job killed by the runner does not."""

    def setUp(self):
        self._fetch = pr_triage.fetch_open_build_prs
        self._repo = pr_triage._repo
        self._gh = pr_triage._gh
        self._deadline = pr_triage.DEADLINE_SEC
        self._upsert = pr_triage.upsert_digest_issue
        self.digests = []
        pr_triage._repo = lambda: REPO
        pr_triage._gh = _Spy()
        pr_triage.upsert_digest_issue = lambda repo, body: self.digests.append(body)
        rows = [_row(i, [], files=[{"path": f"app/x{i}.py", "additions": 90}],
                     statusCheckRollup=[{"status": "COMPLETED", "conclusion": "SUCCESS"}])
                for i in (11, 12, 13)]
        pr_triage.fetch_open_build_prs = lambda repo: (rows, "full", [])

    def tearDown(self):
        pr_triage.fetch_open_build_prs = self._fetch
        pr_triage._repo = self._repo
        pr_triage._gh = self._gh
        pr_triage.DEADLINE_SEC = self._deadline
        pr_triage.upsert_digest_issue = self._upsert

    def test_budget_spent_still_writes_a_complete_digest_and_exits_0(self):
        pr_triage.DEADLINE_SEC = 0.0  # every PR is already over budget
        rc = pr_triage.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.digests), 1, "the digest must be written anyway")
        body = self.digests[0]
        for n in (11, 12, 13):
            self.assertIn(f"#{n}", body, "classification is complete even when writes are not")
        self.assertIn("Label writes deferred", body)

    def test_within_budget_labels_everything_and_says_nothing_about_deferral(self):
        pr_triage.DEADLINE_SEC = 10_000.0
        rc = pr_triage.main()
        self.assertEqual(rc, 0)
        self.assertNotIn("Label writes deferred", self.digests[0])


class FetchBudget(unittest.TestCase):
    def setUp(self):
        self._orig = pr_triage._gh

    def tearDown(self):
        pr_triage._gh = self._orig

    def test_combined_query_is_not_retried_into_the_budget(self):
        seen = {}

        def fake(*args, **kw):
            if "statusCheckRollup" in " ".join(args):
                seen["retries"] = kw.get("retries")
                return _cp(1, "", "ERROR: HTTP 504: 504 Gateway Timeout")
            return _cp(0, "[]")

        pr_triage._gh = fake
        pr_triage.fetch_open_build_prs(REPO)
        self.assertIsNotNone(seen.get("retries"), "must pass retries explicitly")
        self.assertLessEqual(seen["retries"], 1,
                             "the degraded path IS the retry; 4 more costs 71s of an 8-min job")




class WorkflowContract(unittest.TestCase):
    """The tool's self-imposed budget only helps if it is INSIDE the runner's."""

    def _wf(self):
        import yaml
        p = os.path.join(os.path.dirname(__file__), "..", ".github",
                         "workflows", "pr-triage.yml")
        with open(p, encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _step(self):
        for s in self._wf()["jobs"]["triage"]["steps"]:
            if "pr_triage.py" in (s.get("run") or ""):
                return s
        self.fail("no step runs pr_triage.py")

    def test_runs_unbuffered(self):
        # Without -u a timeout-killed run emits nothing at all. That is how
        # 2026-07-28T06:11Z and 06:23Z became forensic blackouts.
        self.assertIn("python -u", self._step()["run"])

    def test_self_deadline_is_strictly_inside_the_job_timeout(self):
        budget = float(self._step()["env"]["PR_TRIAGE_DEADLINE_SEC"])
        timeout = float(self._wf()["jobs"]["triage"]["timeout-minutes"]) * 60
        self.assertLess(budget, timeout,
                        "a budget at or past the runner timeout cannot save the digest")
        self.assertLessEqual(budget, timeout - 60,
                             "leave at least a minute to write the digest and summary")




class ConsoleEncoding(unittest.TestCase):
    """The digest is emoji-heavy; the tool must survive a non-UTF-8 console."""

    def test_streams_are_forced_to_utf8(self):
        # Run the real entrypoint under a cp1252 stdout -- the exact shape that
        # crashed on the tower on 2026-07-28T08:0xZ, after the digest had
        # already been upserted.
        import subprocess as sp
        tool = os.path.join(os.path.dirname(__file__), "..", "tools", "pr_triage.py")
        code = (
            "import sys, io, os;"
            "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='cp1252');"
            "sys.path.insert(0, os.path.dirname(r'" + tool + "'));"
            "import pr_triage;"
            "pr_triage._repo = lambda: 'o/r';"
            "pr_triage.fetch_open_build_prs = lambda r: ([], 'full', []);"
            "pr_triage.main();"
            "print('\\U0001f916 emoji survived')"
        )
        r = sp.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertNotIn("UnicodeEncodeError", r.stderr)


if __name__ == "__main__":
    unittest.main()


