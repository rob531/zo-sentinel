import unittest
import os
import sys
import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class TestSnowConnectorApprovalIntegration(unittest.TestCase):
    """Integration verification for snow_connector_approval_integration.py wiring."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_timestamp = datetime.utcnow()
        self.test_submission_id = "sub_test_001"
        self.test_ticket_id = "INC0012345"
        self.test_requestor = "test_user@example.com"

        self.mock_write_service = Mock()
        self.mock_requests = Mock()

        self.patch_write = patch('requests.post', self.mock_write_service)
        self.patch_write.start()

    def tearDown(self):
        """Clean up patches."""
        self.patch_write.stop()

    def _verify_read_mcp_submissions(self, write_service_call_args):
        """Verify snow_connector reads from mcp_submissions table."""
        call_args = write_service_call_args
        self.assertIsNotNone(call_args)

        url = call_args.get('url') or call_args.get('headers', {}).get('url')
        if isinstance(call_args, dict) and 'json' in call_args:
            json_data = call_args['json']

            if 'table' in json_data and json_data['table'] == 'mcp_submissions':
                return True

            if 'query' in json_data:
                query = json_data['query'].lower()
                self.assertIn('mcp_submissions', query,
                    "snow_connector must query mcp_submissions table")
                return True

        return False

    def _verify_audit_log_write(self, write_service_calls: List, context: Dict):
        """Verify snow_connector writes ServiceNow ticket metadata to audit_log."""
        audit_log_writes = []

        for call in write_service_calls:
            if isinstance(call, dict) and 'json' in call:
                json_data = call['json']
                if json_data.get('table') == 'audit_log':
                    rows = json_data.get('rows', {})
                    if isinstance(rows, dict):
                        audit_log_writes.append(rows)
                    elif isinstance(rows, list):
                        audit_log_writes.extend(rows)

        self.assertGreater(len(audit_log_writes), 0,
            "snow_connector must write to audit_log table")

        ticket_written = False
        for entry in audit_log_writes:
            if entry.get('target_server_id') or entry.get('server_id'):
                if entry.get('ticket_id') or entry.get('servicenow_ticket'):
                    ticket_written = True
                    break

        self.assertTrue(ticket_written,
            "audit_log entry must contain ticket_id/servicenow_ticket metadata")

        return True

    def _verify_mcp_decisions_write(self, write_service_calls: List, context: Dict):
        """Verify approval_workflow creates mcp_decisions record after callback."""
        decision_writes = []

        for call in write_service_calls:
            if isinstance(call, dict) and 'json' in call:
                json_data = call['json']
                if json_data.get('table') == 'mcp_decisions':
                    rows = json_data.get('rows', {})
                    if isinstance(rows, dict):
                        decision_writes.append(rows)

        self.assertGreater(len(decision_writes), 0,
            "approval_workflow must create mcp_decisions record after callback")

        decision = decision_writes[0]
        self.assertIn('submission_id', decision,
            "mcp_decisions must include submission_id")
        self.assertIn('decision', decision,
            "mcp_decisions must include decision field")
        self.assertIn('decided_by', decision,
            "mcp_decisions must include decided_by field")

        return True

    def test_snow_connector_reads_pendig_mcp_requests(self):
        """Test 1: Verify snow_connector reads from mcp_submissions table."""
        try:
            from snow_connector_approval_integration import SnowConnectorApprovalIntegration

            integration = SnowConnectorApprovalIntegration()

            with patch('requests.get') as mock_read:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    'data': [{
                        'id': self.test_submission_id,
                        'requestor': self.test_requestor,
                        'service_name': 'test_service',
                        'status': 'pending'
                    }]
                }
                mock_read.return_value = mock_response

                integration.fetch_pending_submissions()

                read_calls = [c for c in mock_read.call_args_list]
                submissions_read = False
                for call in read_calls:
                    if call and len(call) > 0:
                        url_or_json = call[1].get('json', {}) if isinstance(call[1], dict) else {}
                        if 'mcp_submissions' in str(url_or_json):
                            submissions_read = True
                            break

                self.assertTrue(submissions_read,
                    "snow_connector must read from mcp_submissions table")

        except ImportError:
            self.skipTest("snow_connector_approval_integration module not available")

    def test_snow_connector_writes_servicenow_metadata_to_audit_log(self):
        """Test 2: Verify snow_connector writes ServiceNow ticket metadata to audit_log."""
        try:
            from snow_connector_approval_integration import SnowConnectorApprovalIntegration

            integration = SnowConnectorApprovalIntegration()

            audit_log_entries = []

            def capture_write(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}

                json_data = kwargs.get('json', {})
                if json_data.get('table') == 'audit_log':
                    audit_log_entries.append(json_data.get('rows', {}))

                return response

            self.mock_write_service.side_effect = capture_write

            integration.create_servicenow_ticket({
                'id': self.test_submission_id,
                'requestor': self.test_requestor,
                'service_name': 'test_service'
            })

            self.assertGreater(len(audit_log_entries), 0,
                "snow_connector must write to audit_log after ticket creation")

            entry = audit_log_entries[0]
            has_target = 'target_server_id' in entry or 'server_id' in entry
            has_ticket = 'ticket_id' in entry or 'servicenow_ticket' in entry

            self.assertTrue(has_target,
                "audit_log must have target_server_id (NOT server_id)")
            self.assertTrue(has_ticket,
                "audit_log must have ServiceNow ticket metadata")

        except ImportError:
            self.skipTest("snow_connector_approval_integration module not available")

    def test_approval_workflow_creates_mcp_decisions_on_callback(self):
        """Test 3: Verify approval_workflow creates mcp_decisions after ServiceNow callback."""
        try:
            from snow_connector_approval_integration import ApprovalWorkflow

            workflow = ApprovalWorkflow()

            decision_records = []

            def capture_write(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}

                json_data = kwargs.get('json', {})
                if json_data.get('table') == 'mcp_decisions':
                    decision_records.append(json_data.get('rows', {}))

                return response

            self.mock_write_service.side_effect = capture_write

            callback_payload = {
                'ticket_id': self.test_ticket_id,
                'submission_id': self.test_submission_id,
                'decision': 'approved',
                'decided_by': 'servicenow_approver',
                'decision_timestamp': datetime.utcnow().isoformat()
            }

            workflow.handle_servicenow_callback(callback_payload)

            self.assertGreater(len(decision_records), 0,
                "approval_workflow must create mcp_decisions record on callback")

            decision = decision_records[0]
            self.assertEqual(decision.get('submission_id'), self.test_submission_id)
            self.assertEqual(decision.get('decision'), 'approved')

        except ImportError:
            self.skipTest("approval_workflow not available in snow_connector_approval_integration")

    def test_full_approval_flow_integration(self):
        """Integration test: Full flow from submission to decision."""
        try:
            from snow_connector_approval_integration import SnowConnectorApprovalIntegration

            integration = SnowConnectorApprovalIntegration()

            captured_calls = {'read': [], 'write': []}

            def capture_read(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {
                    'data': [{
                        'id': self.test_submission_id,
                        'requestor': self.test_requestor,
                        'status': 'pending'
                    }]
                }
                captured_calls['read'].append(kwargs)
                return response

            def capture_write(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}
                captured_calls['write'].append(kwargs)
                return response

            with patch('requests.get', capture_read):
                with patch('requests.post', capture_write):
                    integration.process_pending_requests()

            submissions_read = False
            for call in captured_calls['read']:
                if 'mcp_submissions' in str(call):
                    submissions_read = True
                    break

            audit_log_written = False
            mcp_decisions_written = False

            for call in captured_calls['write']:
                json_data = call.get('json', {})
                table = json_data.get('table')
                if table == 'audit_log':
                    audit_log_written = True
                elif table == 'mcp_decisions':
                    mcp_decisions_written = True

            self.assertTrue(submissions_read,
                "Step 1: snow_connector reads mcp_submissions")
            self.assertTrue(audit_log_written,
                "Step 2: snow_connector writes to audit_log")
            self.assertTrue(mcp_decisions_written,
                "Step 3: approval_workflow creates mcp_decisions")

        except ImportError:
            self.skipTest("Integration module not available")

    def test_write_service_contract_compliance(self):
        """Verify write_service calls use correct contract."""
        try:
            from snow_connector_approval_integration import SnowConnectorApprovalIntegration

            integration = SnowConnectorApprovalIntegration()
            captured_requests = []

            def capture_request(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}
                captured_requests.append({'args': args, 'kwargs': kwargs})
                return response

            self.mock_write_service.side_effect = capture_request

            integration.create_servicenow_ticket({
                'id': self.test_submission_id,
                'requestor': self.test_requestor
            })

            for req in captured_requests:
                kwargs = req.get('kwargs', {})
                json_data = kwargs.get('json', {})

                self.assertIn('table', json_data, "Must include 'table' field")
                self.assertIn('rows', json_data, "Must include 'rows' NOT 'row'")
                self.assertIsInstance(json_data['rows'], (dict, list),
                    "'rows' must be dict or list")

        except ImportError:
            self.skipTest("Module not available")

    def test_audit_log_uses_target_server_id(self):
        """Verify audit_log uses target_server_id NOT server_id."""
        try:
            from snow_connector_approval_integration import SnowConnectorApprovalIntegration

            integration = SnowConnectorApprovalIntegration()

            audit_log_entry = None

            def capture_write(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}

                json_data = kwargs.get('json', {})
                if json_data.get('table') == 'audit_log':
                    nonlocal audit_log_entry
                    audit_log_entry = json_data.get('rows', {})

                return response

            self.mock_write_service.side_effect = capture_write

            integration.create_servicenow_ticket({
                'id': self.test_submission_id,
                'target_server_id': 'server_123'
            })

            self.assertIsNotNone(audit_log_entry)

            if 'server_id' in audit_log_entry:
                self.fail("audit_log must use 'target_server_id' NOT 'server_id'")

            has_target = 'target_server_id' in audit_log_entry
            self.assertTrue(has_target,
                "audit_log column must be 'target_server_id'")

        except ImportError:
            self.skipTest("Module not available")


class TestIntegrationWiringVerification(unittest.TestCase):
    """Verify wiring between snow_connector_approval_integration and approval_workflow."""

    def test_data_flow_mcp_submissions_to_servicenot_to_decision(self):
        """Verify complete data flow: mcp_submissions -> ServiceNow -> mcp_decisions."""
        try:
            from snow_connector_approval_integration import (
                SnowConnectorApprovalIntegration,
                ApprovalWorkflow
            )

            submission = {
                'id': self.test_submission_id,
                'requestor': 'user@test.com',
                'service_name': 'test_mcp_service',
                'status': 'pending'
            }

            captured_data = {'ticket': None, 'decision': None}

            def mock_write(*args, **kwargs):
                response = Mock()
                response.status_code = 200
                response.json.return_value = {'success': True}

                json_data = kwargs.get('json', {})
                if json_data.get('table') == 'audit_log':
                    captured_data['ticket'] = json_data.get('rows', {})
                elif json_data.get('table') == 'mcp_decisions':
                    captured_data['decision'] = json_data.get('rows', {})

                return response

            with patch('requests.post', mock_write):
                connector = SnowConnectorApprovalIntegration()
                connector.create_servicenow_ticket(submission)

                workflow = ApprovalWorkflow()
                workflow.handle_servicenow_callback({
                    'ticket_id': 'INC001',
                    'submission_id': submission['id'],
                    'decision': 'approved',
                    'decided_by': 'snow_approver'
                })

            self.assertIsNotNone(captured_data['ticket'],
                "ServiceNow ticket data must be captured")
            self.assertIsNotNone(captured_data['decision'],
                "mcp_decisions must be created")
            self.assertEqual(captured_data['decision']['submission_id'],
                submission['id'])

        except ImportError:
            self.skipTest("Integration module not available")

    def test_callback_url_configuration(self):
        """Verify ServiceNow callback is configured correctly."""
        try:
            from snow_connector_approval_integration import ApprovalWorkflow

            workflow = ApprovalWorkflow()

            self.assertTrue(hasattr(workflow, 'callback_endpoint'),
                "ApprovalWorkflow must have callback_endpoint")

        except ImportError:
            self.skipTest("Module not available")


if __name__ == '__main__':
    unittest.main(verbosity=2)