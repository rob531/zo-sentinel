import pytest
from unittest.mock import patch
from aidr_commit_gateway import AidrCommitGateway
from mcp_server_registry import ServerVerdict

# Fixtures for synthetic server IDs with known verdicts
@pytest.fixture
def trusted_general_server():
    return "server_trusted_general", ServerVerdict.TRUSTED_GENERAL

@pytest.fixture
def trusted_research_server():
    return "server_trusted_research", ServerVerdict.TRUSTED_RESEARCH

@pytest.fixture
def enterprise_controlled_server():
    return "server_enterprise_controlled", ServerVerdict.ENTERPRISE_CONTROLLED

@pytest.fixture
def caution_limited_server():
    return "server_caution_limited", ServerVerdict.CAUTION_LIMITED

@pytest.fixture
def high_risk_isolated_server():
    return "server_high_risk_isolated", ServerVerdict.HIGH_RISK_ISOLATED

def test_verdict_enforcement(
    trusted_general_server,
    trusted_research_server,
    enterprise_controlled_server,
    caution_limited_server,
    high_risk_isolated_server,
):
    # Setup mock write_service
    with patch('aidr_commit_gateway.write_service') as mock_write_service:
        gateway = AidrCommitGateway()

        # Test TRUSTED_GENERAL (should pass)
        server_id, verdict = trusted_general_server
        mock_write_service.get_verdict.return_value = verdict
        result = gateway.check_verdict(server_id)
        assert result == True, f"TRUSTED_GENERAL server {server_id} should be allowed"
        print(f"PASS: TRUSTED_GENERAL server {server_id} allowed")

        # Test TRUSTED_RESEARCH (should pass)
        server_id, verdict = trusted_research_server
        mock_write_service.get_verdict.return_value = verdict
        result = gateway.check_verdict(server_id)
        assert result == True, f"TRUSTED_RESEARCH server {server_id} should be allowed"
        print(f"PASS: TRUSTED_RESEARCH server {server_id} allowed")

        # Test ENTERPRISE_CONTROLLED (should pass)
        server_id, verdict = enterprise_controlled_server
        mock_write_service.get_verdict.return_value = verdict
        result = gateway.check_verdict(server_id)
        assert result == True, f"ENTERPRISE_CONTROLLED server {server_id} should be allowed"
        print(f"PASS: ENTERPRISE_CONTROLLED server {server_id} allowed")

        # Test CAUTION_LIMITED (should fail)
        server_id, verdict = caution_limited_server
        mock_write_service.get_verdict.return_value = verdict
        result = gateway.check_verdict(server_id)
        assert result == False, f"CAUTION_LIMITED server {server_id} should be blocked"
        print(f"PASS: CAUTION_LIMITED server {server_id} blocked")

        # Test HIGH_RISK_ISOLATED (should fail)
        server_id, verdict = high_risk_isolated_server
        mock_write_service.get_verdict.return_value = verdict
        result = gateway.check_verdict(server_id)
        assert result == False, f"HIGH_RISK_ISOLATED server {server_id} should be blocked"
        print(f"PASS: HIGH_RISK_ISOLATED server {server_id} blocked")

if __name__ == "__main__":
    pytest.main([__file__])