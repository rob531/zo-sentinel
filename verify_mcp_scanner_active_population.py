import requests
from datetime import datetime, timedelta

def verify_scanner_population():
    # Query service_health table for mcp_scanner status
    service_health_url = "http://write_service/service_health"
    service_health_params = {"service_name": "mcp_scanner"}
    service_health_response = requests.get(service_health_url, params=service_health_params)
    service_health_data = service_health_response.json()

    # Query mcp_submissions table for recent entries
    mcp_submissions_url = "http://write_service/mcp_submissions"
    time_window = datetime.utcnow() - timedelta(hours=1)
    mcp_submissions_params = {"timestamp": time_window.isoformat()}
    mcp_submissions_response = requests.get(mcp_submissions_url, params=mcp_submissions_params)
    mcp_submissions_data = mcp_submissions_response.json()

    # Prepare the output dictionary
    output = {
        "scanner_health": service_health_data["status"],
        "last_heartbeat": service_health_data["last_heartbeat"],
        "new_submissions_count": len(mcp_submissions_data)
    }

    return output

if __name__ == "__main__":
    result = verify_scanner_population()
    assert result["scanner_health"] == "healthy"
    assert result["new_submissions_count"] > 0
    print("PASS")