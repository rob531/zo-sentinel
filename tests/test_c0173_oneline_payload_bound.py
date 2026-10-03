#!/usr/bin/env python3
"""cycle-0173: a failure list must be bounded by BYTES by its PRODUCER.

friction family `line-count-bound-on-a-oneline-payload` -- 4 hits in the
`deploy-runtime-from-main` lane in the 7 days to 2026-10-02T09:05:03Z, each one
`generate_spine.py --check --strict --quiet` putting 542-547 broken service
names on ONE line behind a `| head -25` that bounded nothing, costing ~8-10k
tokens of that lane's result budget per run.

Every assertion below has a POLE THAT GOES RED, because an assertion never
observed red is UNPROVEN, not passing (HARNESS_DOCTRINE R4):

  * test_negative_control_unbounded_floods   -- the form we removed MUST flood.
  * test_census_detects_the_shape            -- the latch MUST fire on the shape.
  * the other tests then assert the cure, over the same 600-name population.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import bounded  # noqa: E402
from tools import generate_spine as G  # noqa: E402
from tools import oneline_payload_census as C  # noqa: E402

# The population that actually bit: 542 unlisted broken services on 2026-10-02.
N = 600
NAMES = ["svc_axis_scores_%04d" % i for i in range(N)]

# One line of output may be this long, in bytes. Basis: SAMPLE_BYTES (400) plus
# the fixed-shape WITHHELD trailer and the longest literal prefix in main().
MAX_LINE = 1024


def _longest_line(text):
    return max((len(l.encode("utf-8", "replace")) for l in text.splitlines()), default=0)


class NegativeControls(unittest.TestCase):
    """These are the poles. If they stop being red, the cure measures nothing."""

    def test_negative_control_unbounded_floods(self):
        unbounded = ", ".join("%s=import_error" % n for n in NAMES)
        self.assertGreater(
            len(unbounded.encode()), MAX_LINE,
            "the UNBOUNDED join did not flood at n=%d, so MAX_LINE is not a bound "
            "on anything -- this check is UNPROVEN" % N)
        self.assertNotIn("\n", unbounded,
                         "the flood must be ONE line; that is the whole point")

    def test_census_detects_the_shape(self):
        """Positive control: the latch fires on a synthetic in-shape print."""
        bad = ('def f(items):\n'
               '    print("STRICT: %d bad: %s" % (len(items), ", ".join(items)))\n')
        hits = list(C.scan_source(bad, "synthetic.py"))
        self.assertEqual(len(hits), 1,
                         "the census did not flag the canonical in-shape print; "
                         "an unfired detector is not a latch")
        self.assertEqual(hits[0][2], ["items"])

    def test_census_does_not_flag_the_cure(self):
        """Negative control on the detector: the cured form must NOT be flagged."""
        good = ('from bounded import sample\n'
                'def f(items):\n'
                '    print("STRICT: %d bad: %s" % (len(items), sample(items, where="x.json")))\n')
        self.assertEqual(list(C.scan_source(good, "synthetic.py")), [],
                         "the census flags its own cure -- it would never go green")


class BoundedSample(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(bounded.self_test(), 0)

    def test_bounded_is_one_line_and_names_what_it_withheld(self):
        got = bounded.sample(NAMES, where="artifacts/spine_manifest.json")
        self.assertNotIn("\n", got)
        self.assertLessEqual(len(got.encode()), MAX_LINE)
        self.assertIn("WITHHELD", got)
        self.assertIn("of %d" % N, got, "R6: the withheld COUNT must be published")
        self.assertIn("artifacts/spine_manifest.json", got,
                      "R7: the whole list must stay recoverable")

    def test_short_list_is_untouched(self):
        self.assertEqual(bounded.sample(["a", "b"]), "a, b")

    def test_empty_is_none_not_blank(self):
        self.assertEqual(bounded.sample([]), "(none)")


class GenerateSpineStrictOutput(unittest.TestCase):
    """Exercise the REAL main() emitter at the population that bit."""

    def setUp(self):
        self._real = G.build_manifest
        G.build_manifest = lambda: {
            "services": [],
            "service_count": N,
            "ok_count": 0,
            "broken_count": N,
            "known_broken_count": 0,
            "unlisted_broken": [{"name": n, "status": "import_error"} for n in NAMES],
            "stale_known": sorted("stale_%04d" % i for i in range(N)),
        }

    def tearDown(self):
        G.build_manifest = self._real

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = G.main(argv)
        return rc, buf.getvalue()

    def test_strict_quiet_output_is_bounded(self):
        rc, out = self._run(["--strict", "--quiet"])
        self.assertEqual(rc, 1, "the bound must not weaken the verdict: strict "
                                "with %d unlisted broken services is still rc=1" % N)
        self.assertLessEqual(
            _longest_line(out), MAX_LINE,
            "generate_spine --strict --quiet emitted a %dB line; a downstream "
            "`head -N` cannot bound it (friction "
            "line-count-bound-on-a-oneline-payload)" % _longest_line(out))

    def test_the_counts_and_the_recovery_pointer_survive(self):
        _, out = self._run(["--strict", "--quiet"])
        # NEVER assertIn against the captured output: unittest renders the whole
        # haystack on failure, which reproduces the very flood this file proves.
        # Measured 2026-10-03: doing that put 21123B on one line into the result.
        for needle in ("%d UNLISTED" % N, "%d STALE" % N, "WITHHELD",
                       "artifacts/spine_manifest.json"):
            self.assertTrue(needle in out,
                            msg="%r missing from --strict --quiet output "
                                "(%d bytes, longest line %dB)"
                                % (needle, len(out), _longest_line(out)))


class RepoLatch(unittest.TestCase):
    def test_no_in_shape_call_site_remains(self):
        r = C.census(REPO_ROOT)
        # assertTrue, not assertEqual: a list-vs-list failure renders BOTH lists
        # in full, which is this family again. The message carries a bounded
        # sample instead.
        self.assertTrue(
            not r["unparsed"],
            msg="the census could not parse %d file(s); unknown is not zero (R6): %s"
                % (len(r["unparsed"]),
                   bounded.sample(["%s %s" % (p, e) for p, e in r["unparsed"]])))
        self.assertTrue(
            not r["hits"],
            msg="%d new 'N failures: <all N of them>' print(s) landed -- route them "
                "through tools/bounded.py sample(): %s"
                % (len(r["hits"]),
                   bounded.sample(["%s:%d" % (h[0], h[1]) for h in r["hits"]])))
        self.assertGreater(r["scanned"], 50,
                           "the census scanned %d files -- a census that walked "
                           "nothing passes vacuously (R3)" % r["scanned"])


if __name__ == "__main__":
    unittest.main()
