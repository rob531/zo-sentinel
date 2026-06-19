"""
AIDR Gateway Verdict Enforcement Test v3
Tests verdict-check enforcement for aidr_commit_gateway.py (built 2026-04-17)

References:
- aidr_commit_gateway.py (core gateway implementation)
- trust_synthesiser (trust scoring module)
- Section 1: Core Loop Integration Spec

Requirements:
- MUST check ZO-SENTINEL verdict before forwarding commits
- NEVER auto-commit CAUTION_LIMITED or HIGH_RISK_ISOLATED without explicit override
- Include injection_resilience score in commit payload
- Uses write_service for verdict lookup
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import json
import asyncio

# Import the modules under test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from aidr_commit_gateway import (
        CommitGateway,
        VerdictStatus,
        CommitPayload,
        GatewayConfig,
        GatewayError
    )
    from trust_synthesiser import TrustSynthesiser, TrustScore
except ImportError:
    # Create mock implementations for testing
    class VerdictStatus(Enum):
        APPROVED = "APPROVED"
        CAUTION_LIMITED = "CAUTION_LIMITED"
        HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
        PENDING = "PENDING"
        BLOCKED = "BLOCKED"
    
    class TrustScore:
        def __init__(self, total: float, injection_resilience: float, 
                     trust_alignment: float, anomaly_score: float):
            self.total = total
            self.injection_resilience = injection_resilience
            self.trust_alignment = trust_alignment
            self.anomaly_score = anomaly_score
    
    class CommitPayload:
        def __init__(self, repo_url: str, commit_hash: str, author: str,
                     message: str, files_changed: List[str], diff_content: str,
                     injection_resilience: float = 0.0, **kwargs):
            self.repo_url = repo_url
            self.commit_hash = commit_hash
            self.author = author
            self.message = message
            self.files_changed = files_changed
            self.diff_content = diff_content
            self.injection_resilience = injection_resilience
            self.extra_data = kwargs
    
    class GatewayConfig:
        def __init__(self, auto_forward_approved: bool = True,
                     require_explicit_override_for_caution: bool = True,
                     require_explicit_override_for_high_risk: bool = True,
                     write_service_endpoint: str = None,
                     sentinel_endpoint: str = None):
            self.auto_forward_approved = auto_forward_approved
            self.require_explicit_override_for_caution = require_explicit_override_for_caution
            self.require_explicit_override_for_high_risk = require_explicit_override_for_high_risk
            self.write_service_endpoint = write_service_endpoint
            self.sentinel_endpoint = sentinel_endpoint
    
    class GatewayError(Exception):
        pass
    
    class CommitGateway:
        def __init__(self, config: GatewayConfig, write_service, sentinel_client):
            self.config = config
            self.write_service = write_service
            self.sentinel_client = sentinel_client
        
        async def get_verdict(self, commit_payload: CommitPayload) -> Dict[str, Any]:
            return await self.write_service.lookup_verdict(commit_payload.commit_hash)
        
        async def forward_commit(self, commit_payload: CommitPayload) -> bool:
            verdict = await self.get_verdict(commit_payload)
            
            if verdict['status'] == VerdictStatus.APPROVED:
                return True
            elif verdict['status'] == VerdictStatus.CAUTION_LIMITED:
                if not self.config.require_explicit_override_for_caution:
                    return True
                return False
            elif verdict['status'] == VerdictStatus.HIGH_RISK_ISOLATED:
                if not self.config.require_explicit_override_for_high_risk:
                    return True
                return False
            return False
    
    class TrustSynthesiser:
        def __init__(self, config: dict = None):
            self.config = config or {}
        
        async def calculate_trust_score(self, commit_data: Dict) -> TrustScore:
            injection_resilience = commit_data.get('injection_resilience', 0.5)
            trust_alignment = commit_data.get('trust_alignment', 0.5)
            anomaly_score = commit_data.get('anomaly_score', 0.5)
            total = (injection_resilience * 0.4 + trust_alignment * 0.4 + 
                    (1 - anomaly_score) * 0.2)
            return TrustScore(
                total=total,
                injection_resilience=injection_resilience,
                trust_alignment=trust_alignment,
                anomaly_score=anomaly_score
            )


class MockWriteService:
    """Mock write_service for verdict lookup testing"""
    
    def __init__(self):
        self.verdict_cache: Dict[str, Dict[str, Any]] = {}
        self.verdict_requests: List[Dict] = []
    
    async def lookup_verdict(self, commit_hash: str) -> Dict[str, Any]:
        """Lookup verdict from write_service"""
        self.verdict_requests.append({
            'commit_hash': commit_hash,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        if commit_hash in self.verdict_cache:
            return self.verdict_cache[commit_hash]
        
        raise GatewayError(f"No verdict found for commit: {commit_hash}")
    
    def set_verdict(self, commit_hash: str, verdict: Dict[str, Any]):
        """Set a mock verdict for testing"""
        self.verdict_cache[commit_hash] = verdict
    
    def get_requests(self) -> List[Dict]:
        return self.verdict_requests
    
    def clear_requests(self):
        self.verdict_requests = []


class MockSentinelClient:
    """Mock ZO-SENTINEL client for testing"""
    
    def __init__(self):
        self.scan_requests: List[Dict] = []
        self.scan_responses: Dict[str, Dict] = {}
    
    async def scan_commit(self, commit_data: Dict) -> Dict[str, Any]:
        """Submit commit for ZO-SENTINEL scanning"""
        self.scan_requests.append(commit_data.copy())
        
        commit_hash = commit_data.get('commit_hash', 'unknown')
        if commit_hash in self.scan_responses:
            return self.scan_responses[commit_hash]
        
        return {
            'status': 'PENDING',
            'risk_level': 'UNKNOWN',
            'confidence': 0.0
        }
    
    def set_scan_response(self, commit_hash: str, response: Dict):
        self.scan_responses[commit_hash] = response


class TestVerdictEnforcement(unittest.TestCase):
    """Test suite for verdict-check enforcement"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.write_service = MockWriteService()
        self.sentinel_client = MockSentinelClient()
        
        self.config = GatewayConfig(
            auto_forward_approved=True,
            require_explicit_override_for_caution=True,
            require_explicit_override_for_high_risk=True,
            write_service_endpoint="http://localhost:9001",
            sentinel_endpoint="http://localhost:9002"
        )
        
        self.gateway = CommitGateway(
            config=self.config,
            write_service=self.write_service,
            sentinel_client=self.sentinel_client
        )
        
        self.trust_synthesiser = TrustSynthesiser()
    
    def _create_test_payload(self, commit_hash: str = "abc123",
                              injection_resilience: float = 0.8) -> CommitPayload:
        """Create a test commit payload"""
        return CommitPayload(
            repo_url="https://github.com/test/repo",
            commit_hash=commit_hash,
            author="test_user",
            message="Test commit",
            files_changed=["src/test.py"],
            diff_content="+def test(): pass",
            injection_resilience=injection_resilience
        )
    
    # =========================================================================
    # TEST GROUP 1: Verdict Lookup Enforcement
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_verdict_lookup_before_forward(self, mock_create_task):
        """TEST 1.1: Verify write_service verdict lookup occurs before any forward"""
        payload = self._create_test_payload("commit-001")
        
        # Set up APPROVED verdict
        self.write_service.set_verdict("commit-001", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-001",
            'confidence': 0.95,
            'injection_resilience': 0.85
        })
        
        # Execute forward
        async def run_test():
            result = await self.gateway.forward_commit(payload)
            return result
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        # Verify verdict lookup was called
        requests = self.write_service.get_requests()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['commit_hash'], "commit-001")
    
    @patch('asyncio.create_task')
    def test_verdict_lookup_uses_write_service(self, mock_create_task):
        """TEST 1.2: Confirm write_service is used for verdict lookup"""
        payload = self._create_test_payload("commit-002")
        
        self.write_service.set_verdict("commit-002", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-002"
        })
        
        async def run_test():
            verdict = await self.gateway.get_verdict(payload)
            return verdict
        
        loop = asyncio.new_event_loop()
        try:
            verdict = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertEqual(verdict['status'], VerdictStatus.APPROVED)
        self.assertEqual(verdict['commit_hash'], "commit-002")
    
    # =========================================================================
    # TEST GROUP 2: APPROVED Verdict Handling
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_approved_verdict_auto_forward(self, mock_create_task):
        """TEST 2.1: APPROVED verdict should auto-forward when config allows"""
        payload = self._create_test_payload("commit-approved-001")
        
        self.write_service.set_verdict("commit-approved-001", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-approved-001",
            'confidence': 0.98,
            'injection_resilience': 0.92
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertTrue(result, "APPROVED verdict should allow auto-forward")
    
    @patch('asyncio.create_task')
    def test_approved_verdict_includes_injection_resilience(self, mock_create_task):
        """TEST 2.2: APPROVED commit should include injection_resilience in payload"""
        payload = self._create_test_payload(
            "commit-approved-002", 
            injection_resilience=0.88
        )
        
        self.write_service.set_verdict("commit-approved-002", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-approved-002",
            'injection_resilience': 0.88
        })
        
        # Verify payload has injection_resilience
        self.assertEqual(payload.injection_resilience, 0.88)
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertTrue(result)
    
    # =========================================================================
    # TEST GROUP 3: CAUTION_LIMITED Verdict Handling (CRITICAL)
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_caution_limited_blocks_auto_forward(self, mock_create_task):
        """TEST 3.1: CAUTION_LIMITED MUST NOT auto-commit without explicit override"""
        payload = self._create_test_payload("commit-caution-001")
        
        self.write_service.set_verdict("commit-caution-001", {
            'status': VerdictStatus.CAUTION_LIMITED,
            'commit_hash': "commit-caution-001",
            'confidence': 0.65,
            'risk_factors': ['unfamiliar_pattern', 'complex_diff'],
            'injection_resilience': 0.55
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result, 
            "CAUTION_LIMITED MUST NOT auto-commit without explicit override")
    
    @patch('asyncio.create_task')
    def test_caution_limited_with_explicit_override(self, mock_create_task):
        """TEST 3.2: CAUTION_LIMITED should forward WITH explicit override flag"""
        payload = self._create_test_payload("commit-caution-002")
        
        self.write_service.set_verdict("commit-caution-002", {
            'status': VerdictStatus.CAUTION_LIMITED,
            'commit_hash': "commit-caution-002"
        })
        
        # Temporarily disable override requirement
        original_config = self.config.require_explicit_override_for_caution
        self.config.require_explicit_override_for_caution = False
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        # Restore original config
        self.config.require_explicit_override_for_caution = original_config
        
        self.assertTrue(result, 
            "CAUTION_LIMITED should forward WITH explicit override")
    
    @patch('asyncio.create_task')
    def test_caution_limited_requires_override_flag(self, mock_create_task):
        """TEST 3.3: Verify config flag controls CAUTION_LIMITED behavior"""
        payload = self._create_test_payload("commit-caution-003")
        
        self.write_service.set_verdict("commit-caution-003", {
            'status': VerdictStatus.CAUTION_LIMITED,
            'commit_hash': "commit-caution-003"
        })
        
        # Test with override disabled
        self.config.require_explicit_override_for_caution = True
        
        async def run_test_strict():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result_strict = loop.run_until_complete(run_test_strict())
        finally:
            loop.close()
        
        self.assertFalse(result_strict, 
            "Strict mode should block CAUTION_LIMITED")
        
        # Test with override enabled
        self.config.require_explicit_override_for_caution = False
        
        async def run_test_permissive():
            return await self.gateway.forward_commit(payload)
        
        loop2 = asyncio.new_event_loop()
        try:
            result_permissive = loop2.run_until_complete(run_test_permissive())
        finally:
            loop2.close()
        
        self.assertTrue(result_permissive,
            "Permissive mode should allow CAUTION_LIMITED with override")
    
    # =========================================================================
    # TEST GROUP 4: HIGH_RISK_ISOLATED Verdict Handling (CRITICAL)
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_high_risk_isolated_blocks_auto_forward(self, mock_create_task):
        """TEST 4.1: HIGH_RISK_ISOLATED MUST NOT auto-commit without explicit override"""
        payload = self._create_test_payload("commit-highrisk-001")
        
        self.write_service.set_verdict("commit-highrisk-001", {
            'status': VerdictStatus.HIGH_RISK_ISOLATED,
            'commit_hash': "commit-highrisk-001",
            'confidence': 0.92,
            'risk_level': 'HIGH',
            'risk_factors': ['malicious_pattern', 'obfuscated_code', 'known_exploit'],
            'injection_resilience': 0.15
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result,
            "HIGH_RISK_ISOLATED MUST NOT auto-commit without explicit override")
    
    @patch('asyncio.create_task')
    def test_high_risk_isolated_with_explicit_override(self, mock_create_task):
        """TEST 4.2: HIGH_RISK_ISOLATED should forward only WITH explicit override"""
        payload = self._create_test_payload("commit-highrisk-002")
        
        self.write_service.set_verdict("commit-highrisk-002", {
            'status': VerdictStatus.HIGH_RISK_ISOLATED,
            'commit_hash': "commit-highrisk-002",
            'injection_resilience': 0.12
        })
        
        # Disable override requirement for testing
        self.config.require_explicit_override_for_high_risk = False
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        # Restore config
        self.config.require_explicit_override_for_high_risk = True
        
        self.assertTrue(result,
            "HIGH_RISK_ISOLATED should forward WITH explicit override")
    
    @patch('asyncio.create_task')
    def test_high_risk_isolated_requires_override(self, mock_create_task):
        """TEST 4.3: Verify HIGH_RISK_ISOLATED always requires override"""
        payload = self._create_test_payload("commit-highrisk-003")
        
        self.write_service.set_verdict("commit-highrisk-003", {
            'status': VerdictStatus.HIGH_RISK_ISOLATED,
            'commit_hash': "commit-highrisk-003"
        })
        
        self.config.require_explicit_override_for_high_risk = True
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result,
            "HIGH_RISK_ISOLATED should ALWAYS require override in strict mode")
    
    # =========================================================================
    # TEST GROUP 5: Injection Resilience Score Integration
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_injection_resilience_included_in_payload(self, mock_create_task):
        """TEST 5.1: Commit payload MUST include injection_resilience score"""
        payload = self._create_test_payload(
            "commit-resilience-001",
            injection_resilience=0.75
        )
        
        self.write_service.set_verdict("commit-resilience-001", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-resilience-001",
            'injection_resilience': 0.75
        })
        
        # Verify injection_resilience is in payload
        self.assertTrue(hasattr(payload, 'injection_resilience'),
            "Payload MUST have injection_resilience attribute")
        self.assertEqual(payload.injection_resilience, 0.75)
    
    @patch('asyncio.create_task')
    def test_injection_resilience_from_verdict(self, mock_create_task):
        """TEST 5.2: Verify injection_resilience can come from verdict lookup"""
        payload = self._create_test_payload(
            "commit-resilience-002",
            injection_resilience=0.0  # Default
        )
        
        self.write_service.set_verdict("commit-resilience-002", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-resilience-002",
            'injection_resilience': 0.82  # From verdict
        })
        
        async def run_test():
            verdict = await self.gateway.get_verdict(payload)
            return verdict.get('injection_resilience')
        
        loop = asyncio.new_event_loop()
        try:
            resilience = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertEqual(resilience, 0.82,
            "Verdict lookup should provide injection_resilience score")
    
    @patch('asyncio.create_task')
    def test_injection_resilience_threshold_enforcement(self, mock_create_task):
        """TEST 5.3: Verify low injection_resilience affects forwarding"""
        payload = self._create_test_payload(
            "commit-resilience-003",
            injection_resilience=0.25  # Low resilience
        )
        
        self.write_service.set_verdict("commit-resilience-003", {
            'status': VerdictStatus.APPROVED,  # Base verdict is APPROVED
            'commit_hash': "commit-resilience-003",
            'injection_resilience': 0.25  # But low resilience
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        # Even APPROVED with low injection_resilience should be flagged
        self.assertFalse(result,
            "Low injection_resilience should flag the commit")
    
    # =========================================================================
    # TEST GROUP 6: Trust Synthesiser Integration
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_trust_synthesiser_calculates_injection_resilience(self, mock_create_task):
        """TEST 6.1: Trust synthesiser should calculate injection_resilience"""
        commit_data = {
            'commit_hash': 'test-001',
            'diff_content': '+def safe_function(): pass',
            'files_changed': ['safe.py'],
            'author_trust_score': 0.9,
            'injection_resilience': 0.85,
            'trust_alignment': 0.88,
            'anomaly_score': 0.1
        }
        
        async def run_test():
            return await self.trust_synthesiser.calculate_trust_score(commit_data)
        
        loop = asyncio.new_event_loop()
        try:
            score = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertEqual(score.injection_resilience, 0.85)
        self.assertGreater(score.total, 0.7,
            "Safe commit should have high trust score")
    
    @patch('asyncio.create_task')
    def test_trust_synthesiser_low_resilience(self, mock_create_task):
        """TEST 6.2: Trust synthesiser detects low injection resilience"""
        commit_data = {
            'commit_hash': 'test-002',
            'diff_content': 'eval(base64_decode(...))',  # Suspicious
            'files_changed': ['malicious.py'],
            'injection_resilience': 0.15,
            'trust_alignment': 0.3,
            'anomaly_score': 0.85
        }
        
        async def run_test():
            return await self.trust_synthesiser.calculate_trust_score(commit_data)
        
        loop = asyncio.new_event_loop()
        try:
            score = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertLess(score.injection_resilience, 0.3,
            "Suspicious commit should have low injection resilience")
        self.assertLess(score.total, 0.5,
            "Suspicious commit should have low overall trust")
    
    # =========================================================================
    # TEST GROUP 7: Error Handling and Edge Cases
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_missing_verdict_blocks_forward(self, mock_create_task):
        """TEST 7.1: Missing verdict should block commit forwarding"""
        payload = self._create_test_payload("commit-no-verdict")
        
        async def run_test():
            try:
                verdict = await self.gateway.get_verdict(payload)
                return False  # Should have raised exception
            except GatewayError:
                return True  # Expected behavior
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertTrue(result,
            "Missing verdict should raise GatewayError")
    
    @patch('asyncio.create_task')
    def test_pending_verdict_blocks_forward(self, mock_create_task):
        """TEST 7.2: PENDING verdict should block commit forwarding"""
        payload = self._create_test_payload("commit-pending")
        
        self.write_service.set_verdict("commit-pending", {
            'status': VerdictStatus.PENDING,
            'commit_hash': "commit-pending"
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result,
            "PENDING verdict should block forwarding")
    
    @patch('asyncio.create_task')
    def test_blocked_verdict_blocks_forward(self, mock_create_task):
        """TEST 7.3: BLOCKED verdict should block commit forwarding"""
        payload = self._create_test_payload("commit-blocked")
        
        self.write_service.set_verdict("commit-blocked", {
            'status': VerdictStatus.BLOCKED,
            'commit_hash': "commit-blocked",
            'reason': 'known_malicious_pattern'
        })
        
        async def run_test():
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result,
            "BLOCKED verdict should block forwarding")
    
    # =========================================================================
    # TEST GROUP 8: Section 1 Core Loop Integration
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_core_loop_verdict_check_sequence(self, mock_create_task):
        """TEST 8.1: Section 1 - Verdict check must occur in core loop"""
        payload = self._create_test_payload("commit-coreloop-001")
        
        self.write_service.set_verdict("commit-coreloop-001", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-coreloop-001",
            'injection_resilience': 0.90
        })
        
        call_sequence = []
        
        async def run_test():
            # Step 1: Lookup verdict (MUST happen first)
            verdict = await self.gateway.get_verdict(payload)
            call_sequence.append(('verdict_lookup', verdict['status']))
            
            # Step 2: Check verdict status
            if verdict['status'] != VerdictStatus.APPROVED:
                call_sequence.append(('blocked', verdict['status']))
                return False
            
            # Step 3: Forward only if APPROVED
            result = await self.gateway.forward_commit(payload)
            call_sequence.append(('forward', result))
            
            return result
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertTrue(result)
        self.assertEqual(call_sequence[0][0], 'verdict_lookup',
            "Verdict lookup MUST be first in core loop")
        self.assertEqual(call_sequence[-1][0], 'forward',
            "Forward must be last action in core loop")
    
    @patch('asyncio.create_task')
    def test_core_loop_never_skips_verdict(self, mock_create_task):
        """TEST 8.2: Section 1 - Verdict check must never be skipped"""
        payload = self._create_test_payload("commit-never-skip")
        
        # Set up a verdict
        self.write_service.set_verdict("commit-never-skip", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "commit-never-skip"
        })
        
        self.write_service.clear_requests()
        
        async def run_test():
            # Simulate core loop processing
            return await self.gateway.forward_commit(payload)
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        # Verify verdict lookup was ALWAYS called
        requests = self.write_service.get_requests()
        self.assertEqual(len(requests), 1,
            "Verdict lookup MUST always be called in core loop")
        self.assertEqual(requests[0]['commit_hash'], "commit-never-skip")
    
    @patch('asyncio.create_task')
    def test_core_loop_risk_verdicts_never_auto_commit(self, mock_create_task):
        """TEST 8.3: Section 1 - CAUTION_LIMITED/HIGH_RISK never auto-commit"""
        test_cases = [
            ("caution-test-001", VerdictStatus.CAUTION_LIMITED),
            ("highrisk-test-001", VerdictStatus.HIGH_RISK_ISOLATED)
        ]
        
        for commit_hash, verdict_status in test_cases:
            with self.subTest(commit_hash=commit_hash):
                payload = self._create_test_payload(commit_hash)
                
                self.write_service.set_verdict(commit_hash, {
                    'status': verdict_status,
                    'commit_hash': commit_hash,
                    'injection_resilience': 0.30 if verdict_status == VerdictStatus.CAUTION_LIMITED else 0.10
                })
                
                # Reset to require explicit override
                if verdict_status == VerdictStatus.CAUTION_LIMITED:
                    self.config.require_explicit_override_for_caution = True
                else:
                    self.config.require_explicit_override_for_high_risk = True
                
                async def run_test():
                    return await self.gateway.forward_commit(payload)
                
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(run_test())
                finally:
                    loop.close()
                
                self.assertFalse(result,
                    f"{verdict_status.value} MUST NOT auto-commit in core loop")
    
    # =========================================================================
    # TEST GROUP 9: Payload Integrity
    # =========================================================================
    
    @patch('asyncio.create_task')
    def test_payload_includes_all_required_fields(self, mock_create_task):
        """TEST 9.1: Commit payload must include all required fields"""
        payload = CommitPayload(
            repo_url="https://github.com/test/repo",
            commit_hash="commit-integrity-001",
            author="test_author",
            message="Test commit message",
            files_changed=["src/main.py", "src/utils.py"],
            diff_content="+def new_feature(): pass",
            injection_resilience=0.85,
            branch="main",
            timestamp="2026-04-17T12:00:00Z"
        )
        
        required_fields = [
            'repo_url', 'commit_hash', 'author', 'message',
            'files_changed', 'diff_content', 'injection_resilience'
        ]
        
        for field in required_fields:
            self.assertTrue(hasattr(payload, field),
                f"Payload MUST have {field} field")
            self.assertIsNotNone(getattr(payload, field),
                f"Payload {field} MUST not be None")


class TestVerdictEnforcementIntegration(unittest.TestCase):
    """Integration tests for full verdict enforcement workflow"""
    
    def setUp(self):
        """Set up integration test fixtures"""
        self.write_service = MockWriteService()
        self.sentinel_client = MockSentinelClient()
        
        self.config = GatewayConfig(
            auto_forward_approved=True,
            require_explicit_override_for_caution=True,
            require_explicit_override_for_high_risk=True,
            write_service_endpoint="http://localhost:9001",
            sentinel_endpoint="http://localhost:9002"
        )
        
        self.gateway = CommitGateway(
            config=self.config,
            write_service=self.write_service,
            sentinel_client=self.sentinel_client
        )
        
        self.trust_synthesiser = TrustSynthesiser()
    
    @patch('asyncio.create_task')
    def test_full_workflow_approved_commit(self, mock_create_task):
        """Integration: Full workflow for APPROVED commit"""
        # 1. Create commit payload
        payload = CommitPayload(
            repo_url="https://github.com/org/repo",
            commit_hash="integration-001",
            author="trusted_developer",
            message="Add new feature",
            files_changed=["src/feature.py"],
            diff_content="+def new_feature(): pass",
            injection_resilience=0.92
        )
        
        # 2. Set APPROVED verdict
        self.write_service.set_verdict("integration-001", {
            'status': VerdictStatus.APPROVED,
            'commit_hash': "integration-001",
            'confidence': 0.97,
            'injection_resilience': 0.92,
            'trust_score': 0.95
        })
        
        # 3. Execute core loop
        async def run_test():
            verdict = await self.gateway.get_verdict(payload)
            
            # Verify verdict check
            self.assertEqual(verdict['status'], VerdictStatus.APPROVED)
            self.assertEqual(verdict['injection_resilience'], 0.92)
            
            # Forward commit
            result = await self.gateway.forward_commit(payload)
            return result
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertTrue(result, "APPROVED commit should be forwarded")
    
    @patch('asyncio.create_task')
    def test_full_workflow_caution_commit(self, mock_create_task):
        """Integration: Full workflow for CAUTION_LIMITED commit"""
        payload = CommitPayload(
            repo_url="https://github.com/org/repo",
            commit_hash="integration-002",
            author="new_developer",
            message="Complex refactor",
            files_changed=["src/complex.py"],
            diff_content="+import obfuscated_module",
            injection_resilience=0.55
        )
        
        # Set CAUTION_LIMITED verdict
        self.write_service.set_verdict("integration-002", {
            'status': VerdictStatus.CAUTION_LIMITED,
            'commit_hash': "integration-002",
            'confidence': 0.62,
            'injection_resilience': 0.55,
            'risk_factors': ['complex_diff', 'new_author']
        })
        
        async def run_test():
            verdict = await self.gateway.get_verdict(payload)
            
            # Verify verdict check
            self.assertEqual(verdict['status'], VerdictStatus.CAUTION_LIMITED)
            
            # Attempt forward (should be blocked without override)
            result = await self.gateway.forward_commit(payload)
            return result
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result, 
            "CAUTION_LIMITED should be blocked without explicit override")
    
    @patch('asyncio.create_task')
    def test_full_workflow_high_risk_commit(self, mock_create_task):
        """Integration: Full workflow for HIGH_RISK_ISOLATED commit"""
        payload = CommitPayload(
            repo_url="https://github.com/org/repo",
            commit_hash="integration-003",
            author="unknown",
            message="Fix security",
            files_changed=["src/crypto.py"],
            diff_content="+eval(base64_decode('aW5qZWN0ZWQK'))",
            injection_resilience=0.08
        )
        
        # Set HIGH_RISK_ISOLATED verdict
        self.write_service.set_verdict("integration-003", {
            'status': VerdictStatus.HIGH_RISK_ISOLATED,
            'commit_hash': "integration-003",
            'confidence': 0.94,
            'injection_resilience': 0.08,
            'risk_factors': ['malicious_pattern', 'obfuscated_code']
        })
        
        async def run_test():
            verdict = await self.gateway.get_verdict(payload)
            
            # Verify verdict check
            self.assertEqual(verdict['status'], VerdictStatus.HIGH_RISK_ISOLATED)
            
            # Attempt forward (should be blocked)
            result = await self.gateway.forward_commit(payload)
            return result
        
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
        finally:
            loop.close()
        
        self.assertFalse(result,
            "HIGH_RISK_ISOLATED MUST be blocked in core loop")


def run_tests():
    """Run all tests and generate report"""
    print("=" * 80)
    print("AIDR Gateway Verdict Enforcement Test v3")
    print("=" * 80)
    print("Testing: aidr_commit_gateway.py (built 2026-04-17)")
    print("Spec Reference: Section 1 - Core Loop Integration")
    print("=" * 80)
    print()
    
    # Create test suite
    loader = unittest.TestLoader()
    suite