import unittest.mock
import incident_webhook_dispatcher

def test_incident_webhook_dispatcher_wiring():
    # Mock the requests.post function
    with unittest.mock.patch('requests.post') as mock_post:
        # Simulate an mcp_risk_register update
        update_data = {
            'incident_id': '123',
            'status': 'open',
            'severity': 'high',
            'description': 'Test incident'
        }

        # Call the incident_webhook_dispatcher's dispatch function
        incident_webhook_dispatcher.dispatch(update_data)

        # Assert that requests.post was called with the correct arguments
        mock_post.assert_called_once_with(
            'https://example.com/webhook',
            json=update_data,
            headers={'Content-Type': 'application/json'}
        )

if __name__ == '__main__':
    try:
        test_incident_webhook_dispatcher_wiring()
        print("PASS")
    except AssertionError:
        print("FAIL")