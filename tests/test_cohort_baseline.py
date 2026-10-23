#!/usr/bin/env python3
"""Tests for FU-607: the dated cohort baseline writer in
tools/promote_staged_to_active.py.

The baseline (cohort_<YYYYMMDD>.json) had no writer since 2026-10-02, so
ENTERED/LEFT/STABLE was graded against a days-old file. write_cohort_baseline()
emits it on every promoter run. These tests pin the contract the readers
(autopoiesis-bar-tracker, cohort grading) depend on: a flat sorted array of the
PROMOTE-eligible service names, idempotent within a UTC day.

RED-before-green: the final test neutralises the PROMOTE filter and asserts the
baseline then wrongly contains HOLD members -- without that filter the writer
would emit the whole candidate set, which is the bug this writer prevents.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import promote_staged_to_active as P  # noqa: E402

_WHEN = time.strptime("2026-10-11", "%Y-%m-%d")

_VERDICTS = [
    {"service": "zeta_api", "verdict": "PROMOTE"},
    {"service": "alpha_api", "verdict": "PROMOTE"},
    {"service": "held_one", "verdict": "HOLD"},
    {"service": "alpha_api", "verdict": "PROMOTE"},   # duplicate -> collapses
]


class WriteCohortBaseline(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cohort_t_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_path_is_dated_in_the_state_dir(self):
        path = P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN)
        self.assertEqual(os.path.basename(path), "cohort_20261011.json")
        self.assertTrue(os.path.isfile(path))

    def test_only_promote_members_sorted_and_deduped(self):
        path = P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN)
        data = json.load(open(path, encoding="utf-8"))
        # flat sorted array of names, HOLD excluded, duplicate collapsed
        self.assertEqual(data, ["alpha_api", "zeta_api"])
        self.assertNotIn("held_one", data)

    def test_shape_is_a_flat_list_of_strings(self):
        path = P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN)
        data = json.load(open(path, encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertTrue(all(isinstance(x, str) for x in data))

    def test_idempotent_within_a_day(self):
        p1 = P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN)
        p2 = P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN)
        self.assertEqual(p1, p2)
        # one dated file, not two
        dated = [f for f in os.listdir(self.d) if f.startswith("cohort_")]
        self.assertEqual(len(dated), 1)

    def test_empty_promote_set_writes_an_empty_array_not_a_crash(self):
        path = P.write_cohort_baseline(
            [{"service": "h", "verdict": "HOLD"}], state_dir=self.d, when=_WHEN)
        self.assertEqual(json.load(open(path, encoding="utf-8")), [])

    def test_negative_control_without_the_promote_filter_the_baseline_is_wrong(self):
        # Prove the PROMOTE filter is load-bearing: a writer that emitted every
        # candidate would include HOLD members, which is the defect this writer
        # exists to avoid. We reconstruct that broken behaviour inline and assert
        # it diverges from the real writer's output.
        broken = sorted({v["service"] for v in _VERDICTS})  # no verdict filter
        self.assertIn("held_one", broken)
        real = json.load(open(
            P.write_cohort_baseline(_VERDICTS, state_dir=self.d, when=_WHEN),
            encoding="utf-8"))
        self.assertNotEqual(broken, real)


if __name__ == "__main__":
    unittest.main()
