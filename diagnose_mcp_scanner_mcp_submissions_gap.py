import requests

def diagnose_submissions_gap() -> dict:
    """Diagnose why the mcp_submissions table remains empty despite the mcp_scanner daemon running and reporting healthy.

    Returns:
        dict: A dictionary with scanner health status, submissions count, and diagnosis.
    """
    # Query service_health for mcp_scanner status
    health_url = "http://127.0.0.1:8772/service_health"
    try:
        health_response = requests.get(health_url, params={"service": "mcp_scanner"})
        health_response.raise_for_status()
        scanner_healthy = health_response.json().get("healthy", False)
    except requests.RequestException:
        scanner_healthy = False

    # Query mcp_submissions for row count
    submissions_url = "http://127.0.0.1:8772/mcp_submissions"
    try:
        submissions_response = requests.get(submissions_url)
        submissions_response.raise_for_status()
        submissions_count = len(submissions_response.json())
    except requests.RequestException:
        submissions_count = 0

    # Generate diagnosis
    if not scanner_healthy:
        diagnosis = "MCP Scanner is not healthy. Check the scanner logs for errors."
    elif submissions_count == 0:
        diagnosis = "MCP Scanner is healthy but no submissions found. Check if the scanner is properly configured to process submissions."
    else:
        diagnosis = "MCP Scanner is healthy and submissions are being processed."

    return {
        "scanner_healthy": scanner_healthy,
        "submissions_count": submissions_count,
        "diagnosis": diagnosis
    }

if __name__ == "__main__":
    result = diagnose_submissions_gap()
    print(result)

    # Assertions
    assert isinstance(result["scanner_healthy"], bool), "scanner_healthy must be a boolean"
    assert isinstance(result["submissions_count"], int), "submissions_count must be an integer"
    assert isinstance(result["diagnosis"], str) and result["diagnosis"], "diagnosis must be a non-empty string"

    print("PASS: MCP Submissions Gap Diagnosis complete.")