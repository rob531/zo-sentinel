import requests
import json
import unittest
from unittest.mock import patch, MagicMock

class TestAppScoringConsumerLLMIntegration(unittest.TestCase):
    @patch('requests.post')
    def test_verify_app_scoring_consumer_llm_integration(self, mock_post):
        # Seed minimal mcp_llm_axis_scores entry
        seed_data = {
            "server_id": "test_server_123",
            "axis_scores": {
                "axis1": 0.8,
                "axis2": 0.6,
                "axis3": 0.4
            }
        }

        # Mock the write_service/query endpoint response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = seed_data
        mock_post.return_value = mock_response

        # Simulate input to the consumer
        server_id = "test_server_123"

        # Call the app_scoring_consumer.py function
        from app_scoring_consumer import process_server_scores
        risk_tier = process_server_scores(server_id)

        # Assert the correct query was made
        mock_post.assert_called_once_with(
            'http://write_service/query',
            json={"server_id": server_id, "table": "mcp_llm_axis_scores"}
        )

        # Assert the correct risk tier was derived
        expected_risk_tier = "medium"  # Based on the seed axis scores
        self.assertEqual(risk_tier, expected_risk_tier)

        print("PASS")

if __name__ == '__main__':
    unittest.main()