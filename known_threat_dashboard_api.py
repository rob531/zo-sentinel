from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
import requests

app = FastAPI()

# Mock data for testing
mock_threats = [
    {
        "mcp_name": "Threat1",
        "threat_type": "Malware",
        "severity": "High",
        "description": "This is a high severity malware threat."
    },
    {
        "mcp_name": "Threat2",
        "threat_type": "Phishing",
        "severity": "Medium",
        "description": "This is a medium severity phishing threat."
    }
]

@app.get("/known_threats")
async def get_known_threats(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    threat_type: str = Query(None),
    severity: str = Query(None)
):
    # Filter threats based on query parameters
    filtered_threats = mock_threats
    if threat_type:
        filtered_threats = [threat for threat in filtered_threats if threat["threat_type"] == threat_type]
    if severity:
        filtered_threats = [threat for threat in filtered_threats if threat["severity"] == severity]

    # Paginate the results
    start = (page - 1) * per_page
    end = start + per_page
    paginated_threats = filtered_threats[start:end]

    return paginated_threats

if __name__ == "__main__":
    client = TestClient(app)
    response = client.get("/known_threats")
    assert response.status_code == 200
    assert response.json() == mock_threats
    print("PASS")