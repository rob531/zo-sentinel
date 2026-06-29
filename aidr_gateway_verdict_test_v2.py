import unittest
from unittest.mock import patch, MagicMock
from aidr_commit_gateway import AidrCommitGateway
from aidr_verdict_enforcement import VerdictEnforcement

class TestAidrGatewayVerdictEnforcement(unittest.TestCase):
    def setUp(self):
        self.gateway = AidrCommitGateway()
        self.verdict_enforcement = VerdictEnforcement()

    @patch('aidr_commit_gateway.write_service')
    def test_auto_commit_restriction(self, mock_write_service):
        # Test 1: Gateway should NOT auto-commit with CAUTION_LIMITED or HIGH_RISK_ISOLATED
        test_cases = [
            ("CAUTION_LIMITED", False),
            ("HIGH_RISK_ISOLATED", False),
            ("TRUSTED_GENERAL", True),
            ("TRUSTED_RESEARCH", True)
        ]

        for verdict, should_commit in test_cases:
            mock_write_service.query.return_value = {"verdict": verdict}
            with self.subTest(verdict=verdict):
                result = self.gateway._should_auto_commit("test_mcp_id")
                self.assertEqual(result, should_commit)

    @patch('aidr_commit_gateway.write_service')
    def test_injection_resilience_inclusion(self, mock_write_service):
        # Test 2: injection_resilience score should be included for TRUSTED_GENERAL/RESEARCH
        test_cases = [
            ("TRUSTED_GENERAL", True),
            ("TRUSTED_RESEARCH", True),
            ("CAUTION_LIMITED", False),
            ("HIGH_RISK_ISOLATED", False)
        ]

        mock_payload = {"mcp_id": "test123", "verdict": "", "injection_resilience": 0.95}

        for verdict, should_include in test_cases:
            mock_write_service.query.return_value = {"verdict": verdict}
            with self.subTest(verdict=verdict):
                payload = self.gateway._prepare_commit_payload("test123")
                if should_include:
                    self.assertIn("injection_resilience", payload)
                    self.assertEqual(payload["injection_resilience"], 0.95)
                else:
                    self.assertNotIn("injection_resilience", payload)

    @patch('aidr_commit_gateway.write_service')
    def test_verdict_source(self, mock_write_service):
        # Test 3: Gateway should read verdicts from mcp_server_registry via write_service
        mock_write_service.query.return_value = {"verdict": "TRUSTED_GENERAL"}

        verdict = self.gateway._get_verdict("test_mcp_id")
        self.assertEqual(verdict, "TRUSTED_GENERAL")
        mock_write_service.query.assert_called_with(
            "mcp_server_registry",
            "verdicts",
            {"mcp_id": "test_mcp_id"}
        )

    @patch('aidr_commit_gateway.write_service')
    def test_explicit_override(self, mock_write_service):
        # Test explicit override functionality
        mock_write_service.query.return_value = {"verdict": "HIGH_RISK_ISOLATED"}

        # Without override
        result = self.gateway._should_auto_commit("test_mcp_id")
        self.assertFalse(result)

        # With override
        result = self.gateway._should_auto_commit("test_mcp_id", override=True)
        self.assertTrue(result)

if __name__ == '__main__':
    unittest.main()