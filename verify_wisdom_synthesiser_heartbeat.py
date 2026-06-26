import requests
import time
from datetime import datetime, timedelta

def run():
    # Query the service_health table for the last heartbeat of wisdom_synthesiser
    query = """
    SELECT time, value
    FROM service_health
    WHERE service = 'wisdom_synthesiser'
    ORDER BY time DESC
    LIMIT 1
    """
    response = requests.post("http://write_service:8086/query", data={'q': query})

    if response.status_code != 200:
        print("FAIL")
        return

    data = response.json()
    if not data['results'] or not data['results'][0]['series']:
        print("FAIL")
        return

    last_heartbeat_time = data['results'][0]['series'][0]['values'][0][0]
    last_heartbeat_value = data['results'][0]['series'][0]['values'][0][1]

    # Check if the last heartbeat is within the last 5 minutes
    current_time = datetime.utcnow()
    heartbeat_time = datetime.strptime(last_heartbeat_time, "%Y-%m-%dT%H:%M:%S.%fZ")

    if current_time - heartbeat_time <= timedelta(minutes=5) and last_heartbeat_value == 1:
        print("PASS")
    else:
        print("FAIL")

if __name__ == "__main__":
    # Mock the requests.post call to simulate a heartbeat
    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    def mock_post(url, data):
        # Simulate a recent heartbeat
        recent_heartbeat = {
            "results": [
                {
                    "series": [
                        {
                            "values": [
                                [f"{datetime.utcnow() - timedelta(minutes=2)}Z", 1]
                            ]
                        }
                    ]
                }
            ]
        }
        return MockResponse(200, recent_heartbeat)

    # Replace requests.post with the mock
    requests.post = mock_post

    # Run the function and assert the output
    import io
    import sys
    from contextlib import redirect_stdout

    f = io.StringIO()
    with redirect_stdout(f):
        run()
    output = f.getvalue().strip()

    assert output == "PASS", f"Expected 'PASS', got '{output}'"
    print("Test passed!")