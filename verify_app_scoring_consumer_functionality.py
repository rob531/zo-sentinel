import unittest
from unittest.mock import patch
import app_scoring_consumer

class TestAppScoringConsumer(unittest.TestCase):
    @patch('app_scoring_consumer.get_mcp_llm_axis_scores')
    def test_risk_tier_assignment(self, mock_get_scores):
        test_cases = [
            # (mcp_llm_axis_scores, expected_risk_tier)
            ([0.1, 0.2, 0.3, 0.4], 'Low'),
            ([0.5, 0.6, 0.7, 0.8], 'Medium'),
            ([0.9, 1.0, 1.1, 1.2], 'High'),
            ([0.0, 0.0, 0.0, 0.0], 'Low'),
            ([1.0, 1.0, 1.0, 1.0], 'High'),
        ]

        for scores, expected_tier in test_cases:
            mock_get_scores.return_value = scores
            risk_tier = app_scoring_consumer.assign_risk_tier()
            self.assertEqual(risk_tier, expected_tier)

if __name__ == '__main__':
    unittest.main()