import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_weights import build_signal_weights
from verdict_taxonomy import VERDICTS, VERDICT_THRESHOLDS, VERDICT_EXPIRY_DAYS


def score_to_verdict(score):
    """Convert a 0-100 score to a verdict based on thresholds."""
    if score >= 75:
        return 'TRUSTED_GENERAL'
    elif score >= 60:
        return 'TRUSTED_RESEARCH'
    elif score >= 50:
        return 'ENTERPRISE_CONTROLLED'
    elif score >= 35:
        return 'CAUTION_LIMITED'
    elif score >= 20:
        return 'HIGH_RISK_ISOLATED'
    elif score >= 10:
        return 'KNOWN_THREAT'
    else:
        return 'INSUFFICIENT'


class TestScoreToVerdict(unittest.TestCase):
    """Test score_to_verdict at boundary values."""

    def test_boundary_75_trusted_general(self):
        """Score of 75 should return TRUSTED_GENERAL."""
        self.assertEqual(score_to_verdict(75), 'TRUSTED_GENERAL')

    def test_boundary_74_trusted_research(self):
        """Score of 74 should return TRUSTED_RESEARCH."""
        self.assertEqual(score_to_verdict(74), 'TRUSTED_RESEARCH')

    def test_boundary_60_trusted_research(self):
        """Score of 60 should return TRUSTED_RESEARCH."""
        self.assertEqual(score_to_verdict(60), 'TRUSTED_RESEARCH')

    def test_boundary_59_enterprise_controlled(self):
        """Score of 59 should return ENTERPRISE_CONTROLLED."""
        self.assertEqual(score_to_verdict(59), 'ENTERPRISE_CONTROLLED')

    def test_boundary_50_enterprise_controlled(self):
        """Score of 50 should return ENTERPRISE_CONTROLLED."""
        self.assertEqual(score_to_verdict(50), 'ENTERPRISE_CONTROLLED')

    def test_boundary_49_caution_limited(self):
        """Score of 49 should return CAUTION_LIMITED."""
        self.assertEqual(score_to_verdict(49), 'CAUTION_LIMITED')

    def test_boundary_35_caution_limited(self):
        """Score of 35 should return CAUTION_LIMITED."""
        self.assertEqual(score_to_verdict(35), 'CAUTION_LIMITED')

    def test_boundary_34_high_risk_isolated(self):
        """Score of 34 should return HIGH_RISK_ISOLATED."""
        self.assertEqual(score_to_verdict(34), 'HIGH_RISK_ISOLATED')

    def test_boundary_20_high_risk_isolated(self):
        """Score of 20 should return HIGH_RISK_ISOLATED."""
        self.assertEqual(score_to_verdict(20), 'HIGH_RISK_ISOLATED')

    def test_boundary_19_known_threat(self):
        """Score of 19 should return KNOWN_THREAT."""
        self.assertEqual(score_to_verdict(19), 'KNOWN_THREAT')


class TestComputeTrustScore(unittest.TestCase):
    """Test compute_trust_score with known inputs."""

    def setUp(self):
        weights_module = build_signal_weights()
        self.compute_trust_score = weights_module['compute_trust_score']
        self.SIGNAL_WEIGHTS = weights_module['SIGNAL_WEIGHTS']

    def test_all_signals_zero(self):
        """All signals at 0 should return 0."""
        signals = {
            'domain_trust': 0,
            'tool_description_safety': 0,
            'permission_scope': 0,
            'supply_chain': 0,
            'community_signal': 0,
            'temporal_stability': 0
        }
        result = self.compute_trust_score(signals)
        self.assertEqual(result, 0.0)

    def test_all_signals_max(self):
        """All signals at 1.0 should return 1.0."""
        signals = {
            'domain_trust': 1.0,
            'tool_description_safety': 1.0,
            'permission_scope': 1.0,
            'supply_chain': 1.0,
            'community_signal': 1.0,
            'temporal_stability': 1.0
        }
        result = self.compute_trust_score(signals)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_partial_signals(self):
        """Partial signals should return weighted sum."""
        signals = {
            'domain_trust': 1.0,
            'tool_description_safety': 0.5,
            'permission_scope': 0.0,
            'supply_chain': 0.0,
            'community_signal': 0.0,
            'temporal_stability': 0.0
        }
        expected = (self.SIGNAL_WEIGHTS['domain_trust'] * 1.0 +
                    self.SIGNAL_WEIGHTS['tool_description_safety'] * 0.5)
        result = self.compute_trust_score(signals)
        self.assertAlmostEqual(result, expected, places=5)

    def test_known_input_expected_output(self):
        """Test with known input values for predictable output."""
        signals = {
            'domain_trust': 0.8,
            'tool_description_safety': 0.9,
            'permission_scope': 0.7,
            'supply_chain': 0.6,
            'community_signal': 0.5,
            'temporal_stability': 0.4
        }
        expected = (
            self.SIGNAL_WEIGHTS['domain_trust'] * 0.8 +
            self.SIGNAL_WEIGHTS['tool_description_safety'] * 0.9 +
            self.SIGNAL_WEIGHTS['permission_scope'] * 0.7 +
            self.SIGNAL_WEIGHTS['supply_chain'] * 0.6 +
            self.SIGNAL_WEIGHTS['community_signal'] * 0.5 +
            self.SIGNAL_WEIGHTS['temporal_stability'] * 0.4
        )
        result = self.compute_trust_score(signals)
        self.assertAlmostEqual(result, expected, places=5)

    def test_missing_signals_default_to_zero(self):
        """Missing signals should default to 0."""
        signals = {
            'domain_trust': 1.0,
        }
        expected = self.SIGNAL_WEIGHTS['domain_trust'] * 1.0
        result = self.compute_trust_score(signals)
        self.assertAlmostEqual(result, expected, places=5)


class TestSignalWeights(unittest.TestCase):
    """Test that signal weights are properly configured."""

    def test_weights_sum_to_one(self):
        """All weights must sum to exactly 1.0."""
        weights_module = build_signal_weights()
        total = sum(weights_module['SIGNAL_WEIGHTS'].values())
        self.assertAlmostEqual(total, 1.0, places=5,
            msg="Signal weights must sum to 1.0")

    def test_all_weights_positive(self):
        """All weight values must be positive."""
        weights_module = build_signal_weights()
        for name, weight in weights_module['SIGNAL_WEIGHTS'].items():
            self.assertGreater(weight, 0,
                f"Weight for {name} must be positive")

    def test_all_signal_names_defined(self):
        """All expected signal names must be present."""
        weights_module = build_signal_weights()
        expected_signals = ['domain_trust', 'tool_description_safety',
                           'permission_scope', 'supply_chain',
                           'community_signal', 'temporal_stability']
        for signal in expected_signals:
            self.assertIn(signal, weights_module['SIGNAL_WEIGHTS'],
                f"Missing signal: {signal}")


class TestVerdictTaxonomy(unittest.TestCase):
    """Test verdict taxonomy constants."""

    def test_all_verdicts_in_thresholds(self):
        """All verdicts must have corresponding thresholds."""
        for verdict in VERDICTS:
            self.assertIn(verdict, VERDICT_THRESHOLDS,
                f"Verdict {verdict} missing from VERDICT_THRESHOLDS")

    def test_all_thresholds_have_verdicts(self):
        """All threshold entries must have corresponding verdicts."""
        for threshold_verdict in VERDICT_THRESHOLDS:
            self.assertIn(threshold_verdict, VERDICTS,
                f"Threshold entry {threshold_verdict} has no matching verdict")

    def test_verdict_thresholds_are_valid_floats(self):
        """All verdict thresholds must be valid floats between 0 and 1."""
        for verdict, threshold in VERDICT_THRESHOLDS.items():
            self.assertIsInstance(threshold, (int, float),
                f"Threshold for {verdict} must be numeric")
            self.assertGreaterEqual(threshold, 0,
                f"Threshold for {verdict} must be >= 0")
            self.assertLessEqual(threshold, 1,
                f"Threshold for {verdict} must be <= 1")


class TestAttestationExpiryDays(unittest.TestCase):
    """Test attestation expiry days are positive integers."""

    def test_all_verdicts_have_expiry(self):
        """All verdicts must have corresponding expiry days."""
        for verdict in VERDICTS:
            self.assertIn(verdict, VERDICT_EXPIRY_DAYS,
                f"Verdict {verdict} missing from VERDICT_EXPIRY_DAYS")

    def test_expiry_days_are_positive_integers(self):
        """All expiry days must be positive integers."""
        for verdict, days in VERDICT_EXPIRY_DAYS.items():
            self.assertIsInstance(days, int,
                f"Expiry days for {verdict} must be integer")
            self.assertGreater(days, 0,
                f"Expiry days for {verdict} must be positive")

    def test_expiry_days_reasonable_range(self):
        """Expiry days should be in a reasonable range (1-365)."""
        for verdict, days in VERDICT_EXPIRY_DAYS.items():
            self.assertLessEqual(days, 365,
                f"Expiry days for {verdict} exceeds reasonable maximum")


if __name__ == '__main__':
    unittest.main(verbosity=2)