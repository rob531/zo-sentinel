#!/usr/bin/env python3
"""Hermetic two-pole tests for the deterministic scorer. NO network, NO DB, NO keys:
the GLiDE/jev HTTP transport is INJECTED with a fake that returns canned responses.

Run:  python tools/score_deterministic/tests/test_det_scorer.py
      (or: python -m pytest tools/score_deterministic/tests/ -q)

Poles:
  (a) mocked GLiDE returning known labels -> score_server emits the correct
      {axis_name,label,p_top,label_index,probs,...} shape for ALL 7 axes.
  (b) det_validate.agreement_report computes exact% / neighbour% correctly on a
      seeded stored-vs-predicted set.
  (c) gate_rule_v1 + trust_gate map deterministic labels to the right tier
      (CRITICAL escalation; trusted-publisher trust-cap).
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)              # tools/score_deterministic
_TOOLS = os.path.dirname(_PKG)             # tools
_ROOT = os.path.dirname(_TOOLS)            # repo root
for p in (_PKG, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import det_scorer  # noqa: E402
import det_validate  # noqa: E402
from det_scorer import (GlideBackend, JevBackend, make_backend, score_server,  # noqa: E402
                        tier_from_rows)
from rubrics import AXES, AXES_BY_NAME, label_index, rubric_fingerprint  # noqa: E402


# --------------------------------------------------------------------------- #
# fakes: canned GLiDE / jev transports                                        #
# --------------------------------------------------------------------------- #
def make_glide_transport(axis_to_label):
    """Fake /v1/systemone: for whatever axis the request asks about, return a
    peaked `choice` distribution on axis_to_label[axis] (default: first class)."""
    def transport(method, url, headers, body):
        assert method == "POST"
        assert body["model"] == "fastino/GLiDE"
        assert "X-API-Key" in headers  # key is forwarded when provided
        (axis_name, q), = body["questions"].items()
        assert q["type"] == "choice"
        classes = list(q["criteria"].keys())
        want = axis_to_label.get(axis_name, classes[0])
        # peaked but valid distribution summing to 1
        probs = {c: 0.02 for c in classes}
        probs[want] = 1.0 - 0.02 * (len(classes) - 1)
        return {"model": "glide",
                "answers": {axis_name: {"type": "choice", "choice": want,
                                        "confidence": probs[want],
                                        "probabilities": probs}}}
    return transport


def make_glide_transport_dist(axis_to_dist):
    """Fake returning an explicit probability dict per axis (for escalation tests)."""
    def transport(method, url, headers, body):
        (axis_name, q), = body["questions"].items()
        dist = axis_to_dist[axis_name]
        winner = max(dist, key=dist.get)
        return {"model": "glide",
                "answers": {axis_name: {"type": "choice", "choice": winner,
                                        "confidence": dist[winner],
                                        "probabilities": dist}}}
    return transport


def make_jev_transport(axis_label_pyes):
    """Fake jev P(yes)-per-row: returns p_yes keyed off (axis,label) carried in the
    question text. axis_label_pyes: {axis: {label: p_yes}}."""
    def transport(method, url, headers, body):
        q = body["question"]
        # recover which label this row is probing from the embedded "'LABEL'"
        import re
        m = re.search(r"label '([^']+)'", q)
        label = m.group(1)
        # recover axis by matching instructions prefix
        axis = None
        for a in AXES:
            if a.instructions in q:
                axis = a.name
                break
        return {"p_yes": axis_label_pyes.get(axis, {}).get(label, 0.0)}
    return transport


SERVER = {"server_id": "srv_test_1", "name": "Test MCP", "url": "https://example.com",
          "source": "test", "description": "A read-only public weather lookup tool."}


# --------------------------------------------------------------------------- #
# POLE (a): shape for all 7 axes                                              #
# --------------------------------------------------------------------------- #
class TestScorerShape(unittest.TestCase):
    def test_all_seven_axes_emitted_with_correct_shape(self):
        want = {"auth_strength": "STRONG", "capability_breadth": "NARROW",
                "data_sensitivity": "PUBLIC", "network_egress": "NONE",
                "maintainer_trust": "ESTABLISHED", "exploit_surface": "MINIMAL",
                "overall_risk": "LOW"}
        backend = GlideBackend(make_glide_transport(want), api_key="TESTKEY")
        rows = score_server(SERVER, backend)

        self.assertEqual(len(rows), 7)
        got_axes = {r["axis_name"] for r in rows}
        self.assertEqual(got_axes, {a.name for a in AXES})

        required = {"server_id", "axis_name", "label", "label_index", "probs",
                    "p_top", "p_critical", "p_danger", "escalated", "escalated_to",
                    "decision_rule_version", "model_version", "adapter_sha256",
                    "scored_at"}
        for r in rows:
            self.assertEqual(required, set(r.keys()), f"shape drift on {r['axis_name']}")
            ax = AXES_BY_NAME[r["axis_name"]]
            # chosen label is from the verbatim class set and matches what we fed
            self.assertIn(r["label"], ax.classes)
            self.assertEqual(r["label"], want[r["axis_name"]])
            # label_index indexes INTO the verbatim class set
            self.assertEqual(r["label_index"], ax.classes.index(r["label"]))
            self.assertEqual(r["label_index"], label_index(r["axis_name"], r["label"]))
            # p_top is the mass on the chosen label, in [0,1]
            self.assertAlmostEqual(r["p_top"], r["probs"][r["label"]], places=5)
            self.assertGreaterEqual(r["p_top"], 0.0)
            self.assertLessEqual(r["p_top"], 1.0)
            # probs cover exactly the class set and sum ~1
            self.assertEqual(set(r["probs"].keys()), set(ax.classes))
            self.assertAlmostEqual(sum(r["probs"].values()), 1.0, places=4)
            self.assertEqual(r["model_version"], "glide-systemone-v1")
            self.assertEqual(r["decision_rule_version"], "gate_rule_v1")
            self.assertEqual(r["adapter_sha256"], rubric_fingerprint())

    def test_overall_risk_carries_p_critical_others_none(self):
        want = {a.name: a.classes[0] for a in AXES}
        want["overall_risk"] = "LOW"
        backend = GlideBackend(make_glide_transport(want), api_key="K")
        rows = {r["axis_name"]: r for r in score_server(SERVER, backend)}
        self.assertIsNotNone(rows["overall_risk"]["p_critical"])
        self.assertIsNotNone(rows["overall_risk"]["p_danger"])
        for other in ("auth_strength", "data_sensitivity", "maintainer_trust"):
            self.assertIsNone(rows[other]["p_critical"])
            self.assertIsNone(rows[other]["p_danger"])

    def test_jev_backend_same_shape(self):
        # jev returns P(yes) per label; highest P(yes) must win after softmax.
        pyes = {a.name: {c: 0.1 for c in a.classes} for a in AXES}
        pyes["overall_risk"]["HIGH"] = 0.95
        pyes["auth_strength"]["WEAK"] = 0.9
        backend = JevBackend(make_jev_transport(pyes), api_key="JK")
        rows = {r["axis_name"]: r for r in score_server(SERVER, backend)}
        self.assertEqual(rows["overall_risk"]["label"], "HIGH")
        self.assertEqual(rows["auth_strength"]["label"], "WEAK")
        self.assertEqual(rows["overall_risk"]["model_version"], "jev-pyes-v1")
        for r in rows.values():
            self.assertAlmostEqual(sum(r["probs"].values()), 1.0, places=4)

    def test_make_backend_rejects_unknown(self):
        with self.assertRaises(ValueError):
            make_backend("nope", transport=make_glide_transport({}))

    def test_rubric_fingerprint_deterministic(self):
        self.assertEqual(rubric_fingerprint(), rubric_fingerprint())
        self.assertTrue(rubric_fingerprint().startswith("det-"))


# --------------------------------------------------------------------------- #
# POLE (b): agreement math                                                    #
# --------------------------------------------------------------------------- #
class TestAgreement(unittest.TestCase):
    def test_exact_and_neighbour_counts(self):
        # data_sensitivity ladder: PUBLIC < INTERNAL < SENSITIVE < CRITICAL
        pairs = [
            ("data_sensitivity", "PUBLIC", "PUBLIC"),      # exact
            ("data_sensitivity", "INTERNAL", "PUBLIC"),    # neighbour (1 step)
            ("data_sensitivity", "CRITICAL", "PUBLIC"),    # far (3 steps)
            ("data_sensitivity", "SENSITIVE", "SENSITIVE"),  # exact
        ]
        rep = det_validate.agreement_report(pairs)
        ds = rep["axes"]["data_sensitivity"]
        self.assertEqual(ds["n"], 4)
        self.assertEqual(ds["exact"], 2)
        self.assertEqual(ds["exact_pct"], 50.0)
        # neighbour counts exact(2) + the 1-step(1) = 3
        self.assertEqual(ds["neighbour"], 3)
        self.assertEqual(ds["neighbour_pct"], 75.0)
        self.assertTrue(ds["ordinal"])

    def test_non_ordinal_axis_neighbour_equals_exact(self):
        pairs = [
            ("maintainer_trust", "ESTABLISHED", "ESTABLISHED"),  # exact
            ("maintainer_trust", "COMMUNITY", "ESTABLISHED"),    # miss (non-ordinal)
        ]
        rep = det_validate.agreement_report(pairs)
        mt = rep["axes"]["maintainer_trust"]
        self.assertEqual(mt["exact"], 1)
        self.assertEqual(mt["neighbour"], 1)  # no neighbour credit for non-ordinal
        self.assertFalse(mt["ordinal"])

    def test_unknown_sentinel_gets_no_neighbour_credit(self):
        # UNKNOWN is a sentinel, not a ladder rung -> only exact counts.
        pairs = [
            ("auth_strength", "UNKNOWN", "WEAK"),   # sentinel vs rung: not neighbour
            ("auth_strength", "STRONG", "UNKNOWN"),  # rung vs sentinel: not neighbour
            ("auth_strength", "UNKNOWN", "UNKNOWN"),  # exact
        ]
        rep = det_validate.agreement_report(pairs)
        a = rep["axes"]["auth_strength"]
        self.assertEqual(a["exact"], 1)
        self.assertEqual(a["neighbour"], 1)  # only the exact UNKNOWN==UNKNOWN

    def test_overall_rollup(self):
        pairs = [
            ("overall_risk", "LOW", "LOW"),
            ("overall_risk", "MEDIUM", "LOW"),   # neighbour
            ("exploit_surface", "BROAD", "MINIMAL"),  # far
        ]
        rep = det_validate.agreement_report(pairs)
        self.assertEqual(rep["overall"]["n"], 3)
        self.assertEqual(rep["overall"]["exact"], 1)
        self.assertEqual(rep["overall"]["neighbour"], 2)


# --------------------------------------------------------------------------- #
# POLE (c): tier mapping via gate_rule_v1 + trust_gate                        #
# --------------------------------------------------------------------------- #
class TestTierMapping(unittest.TestCase):
    def _rows_for(self, overall_dist, url, name, maint="UNKNOWN_AUTHOR"):
        dist = {a.name: {c: (1.0 if i == 0 else 0.0)
                         for i, c in enumerate(a.classes)} for a in AXES}
        dist["overall_risk"] = overall_dist
        dist["maintainer_trust"] = {c: (1.0 if c == maint else 0.0)
                                    for c in AXES_BY_NAME["maintainer_trust"].classes}
        backend = GlideBackend(make_glide_transport_dist(dist), api_key="K")
        srv = {"server_id": "s", "name": name, "url": url, "description": "d"}
        return srv, score_server(srv, backend)

    def test_critical_escalation(self):
        # CRITICAL mass >= 0.40 -> escalated -> tier CRITICAL (untrusted publisher)
        srv, rows = self._rows_for(
            {"LOW": 0.1, "MEDIUM": 0.1, "HIGH": 0.3, "CRITICAL": 0.5},
            "https://randohost.example/mcp", "rando-tool")
        overall = [r for r in rows if r["axis_name"] == "overall_risk"][0]
        self.assertTrue(overall["escalated"])
        self.assertEqual(overall["escalated_to"], "CRITICAL")
        tier = tier_from_rows(srv, rows)
        self.assertEqual(tier["raw_tier"], "CRITICAL")
        self.assertEqual(tier["published_tier"], "CRITICAL")
        self.assertFalse(tier["capped"])

    def test_no_escalation_below_threshold(self):
        # CRITICAL mass < 0.40 -> NOT escalated -> argmax label wins (HIGH)
        srv, rows = self._rows_for(
            {"LOW": 0.1, "MEDIUM": 0.2, "HIGH": 0.4, "CRITICAL": 0.3},
            "https://randohost.example/mcp", "rando-tool")
        overall = [r for r in rows if r["axis_name"] == "overall_risk"][0]
        self.assertFalse(overall["escalated"])
        tier = tier_from_rows(srv, rows)
        self.assertEqual(tier["raw_tier"], "HIGH")
        self.assertEqual(tier["published_tier"], "HIGH")  # untrusted: no cap

    def test_trusted_publisher_trust_cap(self):
        # Verified GitHub org (microsoft) scored HIGH -> trust_gate caps to MEDIUM.
        srv, rows = self._rows_for(
            {"LOW": 0.1, "MEDIUM": 0.2, "HIGH": 0.6, "CRITICAL": 0.1},
            "https://github.com/microsoft/mssql-mcp", "Azure SQL MCP")
        tier = tier_from_rows(srv, rows)
        self.assertEqual(tier["raw_tier"], "HIGH")
        self.assertEqual(tier["published_tier"], "MEDIUM")
        self.assertTrue(tier["capped"])
        self.assertTrue(tier["trust_basis"].startswith("verified_publisher"))

    def test_masquerade_gets_no_cap(self):
        # homoglyph squat of microsoft -> NOT trusted, flagged, NOT capped
        srv, rows = self._rows_for(
            {"LOW": 0.0, "MEDIUM": 0.1, "HIGH": 0.7, "CRITICAL": 0.2},
            "https://github.com/micr0soft/evil-mcp", "micr0soft")
        tier = tier_from_rows(srv, rows)
        self.assertEqual(tier["published_tier"], "HIGH")
        self.assertFalse(tier["capped"])
        self.assertTrue(tier["masquerade_flag"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
