"""
Verdict-check enforcement test for aidr_commit_gateway.py
Verifies gateway refuses forwarding of CAUTION_LIMITED or HIGH_RISK_ISOLATED verdicts
without explicit override. Uses write_service/query for verdict lookup only.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from enum import Enum

# Import the gateway module
try:
    from aidr_commit_gateway import AIDRGateway, GatewayConfig, GatewayResponse, VerdictType
except ImportError:
    # If module not available, define minimal stubs for testing
    class VerdictType(Enum):
        LOW_RISK = "LOW_RISK"
        CAUTION_LIMITED = "CAUTION_LIMITED"
        HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
        BLOCKED = "BLOCKED"
    
    class GatewayConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    
    class GatewayResponse:
        def __init__(self, success=False, message="", data=None):
            self.success = success
            self.message = message
            self.data = data or {}
            self.timestamp = datetime.utcnow().isoformat()


class VerdictEnforcementTest(unittest.TestCase):
    """Test verdict enforcement on AIDR Gateway commit operations."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = GatewayConfig(
            gateway_id="test-gateway-001",
            enable_verdict_enforcement=True,
            require_explicit_override=True,
            max_retries=3,
            timeout_seconds=30
        )
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_verdict_lookup_uses_query_service(self, mock_query_client, mock_write_client):
        """
        Verify verdict lookup calls query service, not direct DB access.
        CRITICAL: This test ensures compliance with the no-direct-DB-access requirement.
        """
        # Setup mock query service
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-123',
            'verdict_type': 'LOW_RISK',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write_client.return_value = mock_write
        
        # Create gateway and perform lookup
        gateway = AIDRGateway(self.config)
        
        # Call the verdict lookup method
        result = gateway.lookup_verdict('v-123')
        
        # Verify query service was called
        mock_query.get_verdict_by_id.assert_called_once_with('v-123')
        
        # Verify write service was NOT called for lookup
        mock_write.get_verdict_by_id.assert_not_called()
        
        self.assertIsNotNone(result)
        self.assertEqual(result['verdict_type'], 'LOW_RISK')
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_caution_limited_verdict_blocked_without_override(self, mock_query_client, mock_write_client):
        """
        Verify gateway refuses CAUTION_LIMITED verdict without explicit override.
        This is the core enforcement test for CAUTION_LIMITED verdicts.
        """
        # Setup mock query service with CAUTION_LIMITED verdict
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-caution-001',
            'verdict_type': 'CAUTION_LIMITED',
            'description': 'Requires manual review',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit without override
        result = gateway.commit_verdict(
            verdict_id='v-caution-001',
            artifact_id='artifact-123',
            override=False
        )
        
        # Verify commit was refused
        self.assertFalse(result.success)
        self.assertIn('blocked', result.message.lower())
        self.assertIn('caution', result.message.lower())
        
        # Verify write service was NOT called (commit blocked)
        mock_write.commit_verdict.assert_not_called()
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_high_risk_isolated_verdict_blocked_without_override(self, mock_query_client, mock_write_client):
        """
        Verify gateway refuses HIGH_RISK_ISOLATED verdict without explicit override.
        This is the core enforcement test for HIGH_RISK_ISOLATED verdicts.
        """
        # Setup mock query service with HIGH_RISK_ISOLATED verdict
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-highrisk-001',
            'verdict_type': 'HIGH_RISK_ISOLATED',
            'description': 'Critical risk - isolated handling required',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit without override
        result = gateway.commit_verdict(
            verdict_id='v-highrisk-001',
            artifact_id='artifact-456',
            override=False
        )
        
        # Verify commit was refused
        self.assertFalse(result.success)
        self.assertIn('blocked', result.message.lower())
        self.assertIn('high', result.message.lower())
        
        # Verify write service was NOT called
        mock_write.commit_verdict.assert_not_called()
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_caution_limited_approved_with_override(self, mock_query_client, mock_write_client):
        """
        Verify CAUTION_LIMITED verdict CAN be forwarded with explicit override.
        """
        # Setup mock query service
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-caution-002',
            'verdict_type': 'CAUTION_LIMITED',
            'description': 'Requires manual review',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service to succeed
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-abc-123'
        }
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit WITH override
        result = gateway.commit_verdict(
            verdict_id='v-caution-002',
            artifact_id='artifact-789',
            override=True,
            override_reason='Manual review completed - safe to proceed'
        )
        
        # Verify commit succeeded
        self.assertTrue(result.success)
        
        # Verify write service WAS called
        mock_write.commit_verdict.assert_called_once()
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_high_risk_isolated_approved_with_override(self, mock_query_client, mock_write_client):
        """
        Verify HIGH_RISK_ISOLATED verdict CAN be forwarded with explicit override.
        This ensures legitimate high-risk items can proceed with proper authorization.
        """
        # Setup mock query service
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-highrisk-002',
            'verdict_type': 'HIGH_RISK_ISOLATED',
            'description': 'Critical risk - isolated handling required',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-def-456'
        }
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit WITH override
        result = gateway.commit_verdict(
            verdict_id='v-highrisk-002',
            artifact_id='artifact-xyz',
            override=True,
            override_reason='Emergency authorization - risk accepted',
            authorized_by='security-admin@company.com'
        )
        
        # Verify commit succeeded
        self.assertTrue(result.success)
        
        # Verify write service WAS called
        mock_write.commit_verdict.assert_called_once()
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_low_risk_verdict_allowed_without_override(self, mock_query_client, mock_write_client):
        """
        Verify LOW_RISK verdict is forwarded without requiring override.
        Standard workflow should not require explicit override for low-risk items.
        """
        # Setup mock query service
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-low-001',
            'verdict_type': 'LOW_RISK',
            'description': 'Standard processing',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-low-001'
        }
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit WITHOUT override
        result = gateway.commit_verdict(
            verdict_id='v-low-001',
            artifact_id='artifact-low-001',
            override=False
        )
        
        # Verify commit succeeded
        self.assertTrue(result.success)
        
        # Verify write service WAS called
        mock_write.commit_verdict.assert_called_once()
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_verdict_enforcement_disabled_allows_all(self, mock_query_client, mock_write_client):
        """
        Verify that when enforcement is disabled, all verdicts are forwarded.
        This is a safety valve test for maintenance scenarios.
        """
        # Create config with enforcement disabled
        config = GatewayConfig(
            gateway_id="test-gateway-002",
            enable_verdict_enforcement=False,
            require_explicit_override=False
        )
        
        # Setup mock query service with HIGH_RISK_ISOLATED
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-highrisk-disabled',
            'verdict_type': 'HIGH_RISK_ISOLATED',
            'description': 'Critical risk',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-disabled-001'
        }
        mock_write_client.return_value = mock_write
        
        # Create gateway with enforcement disabled
        gateway = AIDRGateway(config)
        
        # Attempt commit without override - should succeed
        result = gateway.commit_verdict(
            verdict_id='v-highrisk-disabled',
            artifact_id='artifact-disabled',
            override=False
        )
        
        # Verify commit succeeded even without override
        self.assertTrue(result.success)
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_verdict_type_enum_coverage(self, mock_query_client, mock_write_client):
        """
        Verify all verdict types are properly enumerated and handled.
        """
        expected_verdicts = ['LOW_RISK', 'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'BLOCKED']
        
        for verdict_type in expected_verdicts:
            # Setup mock for each verdict type
            mock_query = Mock()
            mock_query.get_verdict_by_id.return_value = {
                'verdict_id': f'v-{verdict_type.lower()}',
                'verdict_type': verdict_type,
                'created_at': datetime.utcnow().isoformat()
            }
            mock_query_client.return_value = mock_query
            
            # Verify the verdict type is valid
            self.assertIn(verdict_type, [v.value for v in VerdictType])
            
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_audit_log_for_blocked_verdicts(self, mock_query_client, mock_write_client):
        """
        Verify blocked verdicts are properly logged for audit trail.
        """
        # Setup mock query service
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-audit-test',
            'verdict_type': 'HIGH_RISK_ISOLATED',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt blocked commit
        result = gateway.commit_verdict(
            verdict_id='v-audit-test',
            artifact_id='artifact-audit',
            override=False
        )
        
        # Verify audit data is included in response
        self.assertIn('audit', result.data)
        audit_data = result.data['audit']
        self.assertEqual(audit_data['verdict_type'], 'HIGH_RISK_ISOLATED')
        self.assertEqual(audit_data['action'], 'BLOCKED')
        self.assertIn('timestamp', audit_data)
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_query_service_failure_handling(self, mock_query_client, mock_write_client):
        """
        Verify gateway handles query service failures gracefully.
        """
        # Setup mock to raise exception
        mock_query = Mock()
        mock_query.get_verdict_by_id.side_effect = Exception("Query service unavailable")
        mock_query_client.return_value = mock_query
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Attempt commit - should fail safely
        result = gateway.commit_verdict(
            verdict_id='v-unavailable',
            artifact_id='artifact-fail',
            override=False
        )
        
        # Verify failure is handled
        self.assertFalse(result.success)
        self.assertIn('error', result.message.lower())
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_write_service_never_called_for_blocked(self, mock_query_client, mock_write_client):
        """
        CRITICAL: Verify write_service is NEVER called when verdict is blocked.
        This ensures the enforcement gate works correctly.
        """
        # Setup mock for blocked verdict
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-never-call-write',
            'verdict_type': 'HIGH_RISK_ISOLATED',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        # Setup mock write service
        mock_write = Mock()
        mock_write_client.return_value = mock_write
        
        # Create gateway
        gateway = AIDRGateway(self.config)
        
        # Perform commit attempt
        gateway.commit_verdict(
            verdict_id='v-never-call-write',
            artifact_id='artifact-never-write',
            override=False
        )
        
        # CRITICAL ASSERTION: write_service should never be called
        mock_write.commit_verdict.assert_not_called()
        mock_write.create.assert_not_called()
        mock_write.update.assert_not_called()
        # Verify query service WAS called (for verdict lookup)
        mock_query.get_verdict_by_id.assert_called()


class VerdictEnforcementIntegrationTest(unittest.TestCase):
    """
    Integration tests for verdict enforcement workflow.
    These tests verify end-to-end enforcement scenarios.
    """
    
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_full_workflow_caution_to_approval(self, mock_query_client, mock_write_client):
        """
        Test complete workflow: verdict lookup -> blocked -> override -> approved.
        """
        # Setup sequence of verdict responses
        verdict_responses = [
            # First lookup: CAUTION_LIMITED
            {
                'verdict_id': 'v-workflow-001',
                'verdict_type': 'CAUTION_LIMITED',
                'created_at': datetime.utcnow().isoformat()
            },
            # Second lookup: Same verdict (still caution)
            {
                'verdict_id': 'v-workflow-001',
                'verdict_type': 'CAUTION_LIMITED',
                'created_at': datetime.utcnow().isoformat()
            }
        ]
        
        mock_query = Mock()
        mock_query.get_verdict_by_id.side_effect = verdict_responses
        mock_query_client.return_value = mock_query
        
        # Setup write service
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-workflow-001'
        }
        mock_write_client.return_value = mock_write
        
        # Create gateway
        config = GatewayConfig(
            gateway_id="workflow-gateway",
            enable_verdict_enforcement=True
        )
        gateway = AIDRGateway(config)
        
        # Step 1: Attempt without override - should block
        result1 = gateway.commit_verdict(
            verdict_id='v-workflow-001',
            artifact_id='artifact-workflow',
            override=False
        )
        self.assertFalse(result1.success)
        
        # Step 2: Attempt with override - should succeed
        result2 = gateway.commit_verdict(
            verdict_id='v-workflow-001',
            artifact_id='artifact-workflow',
            override=True,
            override_reason='Manual review completed'
        )
        self.assertTrue(result2.success)
        
    @patch('aidr_commit_gateway.WriteServiceClient')
    @patch('aidr_commit_gateway.QueryServiceClient')
    def test_concurrent_commit_same_verdict(self, mock_query_client, mock_write_client):
        """
        Test handling of concurrent commits for the same verdict.
        """
        mock_query = Mock()
        mock_query.get_verdict_by_id.return_value = {
            'verdict_id': 'v-concurrent',
            'verdict_type': 'CAUTION_LIMITED',
            'created_at': datetime.utcnow().isoformat()
        }
        mock_query_client.return_value = mock_query
        
        mock_write = Mock()
        mock_write.commit_verdict.return_value = {
            'success': True,
            'commit_id': 'commit-concurrent'
        }
        mock_write_client.return_value = mock_write
        
        config = GatewayConfig(
            gateway_id="concurrent-gateway",
            enable_verdict_enforcement=True
        )
        gateway = AIDRGateway(config)
        
        # Simulate concurrent requests
        import threading
        
        results = []
        errors = []
        
        def attempt_commit(override_flag):
            try:
                result = gateway.commit_verdict(
                    verdict_id='v-concurrent',
                    artifact_id='artifact-concurrent',
                    override=override_flag
                )
                results.append(result)
            except Exception as e:
                errors.append(str(e))
        
        # Start threads
        threads = [
            threading.Thread(target=attempt_commit, args=(False,)),
            threading.Thread(target=attempt_commit, args=(True,)),
            threading.Thread(target=attempt_commit, args=(False,))
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Verify no errors occurred
        self.assertEqual(len(errors), 0)
        
        # Verify results are consistent
        success_count = sum(1 for r in results if r.success)
        fail_count = sum(1 for r in results if not r.success)
        # At least one should fail (the ones without override)
        self.assertGreater(fail_count, 0)


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)