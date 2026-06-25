from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from fastapi.testclient import TestClient
import uvicorn

app = FastAPI()

class SignalContribution(BaseModel):
    score: float
    confidence: float
    evidence: Dict[str, Any]

class ServerVerdictSignals(BaseModel):
    domain_trust: SignalContribution
    tool_description_safety: SignalContribution
    permission_scope: SignalContribution
    supply_chain: SignalContribution
    community_signal: SignalContribution
    temporal_stability: SignalContribution
    injection_resilience: SignalContribution

# Mock write_service for testing purposes
class MockWriteService:
    def get_server_signal_scores(self, server_id: str):
        # Mock data for testing
        mock_data = {
            "domain_trust": {
                "score": 0.85,
                "confidence": 0.9,
                "evidence": {"domain_age": 5, "reputation": "high"}
            },
            "tool_description_safety": {
                "score": 0.75,
                "confidence": 0.8,
                "evidence": {"description_length": 200, "keywords": ["safe", "secure"]}
            },
            "permission_scope": {
                "score": 0.9,
                "confidence": 0.85,
                "evidence": {"permissions_requested": 3, "permissions_granted": 2}
            },
            "supply_chain": {
                "score": 0.8,
                "confidence": 0.8,
                "evidence": {"dependencies": 10, "vulnerabilities": 0}
            },
            "community_signal": {
                "score": 0.7,
                "confidence": 0.75,
                "evidence": {"reviews": 50, "rating": 4.5}
            },
            "temporal_stability": {
                "score": 0.95,
                "confidence": 0.9,
                "evidence": {"uptime": 99.9, "downtime_events": 1}
            },
            "injection_resilience": {
                "score": 0.88,
                "confidence": 0.87,
                "evidence": {"injection_attempts": 0, "resilience_tests_passed": 5}
            }
        }
        return mock_data

write_service = MockWriteService()

@app.get("/servers/{server_id}/verdict/signals", response_model=ServerVerdictSignals)
async def get_verdict_signals(server_id: str):
    try:
        signal_scores = write_service.get_server_signal_scores(server_id)
        return signal_scores
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Test the API
    client = TestClient(app)

    # Test with a seeded server_id
    test_server_id = "test_server_123"
    response = client.get(f"/servers/{test_server_id}/verdict/signals")

    assert response.status_code == 200
    assert response.json() is not None

    # Check that all 7 signals are present in the response
    required_signals = [
        "domain_trust",
        "tool_description_safety",
        "permission_scope",
        "supply_chain",
        "community_signal",
        "temporal_stability",
        "injection_resilience"
    ]
    for signal in required_signals:
        assert signal in response.json()

    print("All tests passed successfully!")
    uvicorn.run(app, host="0.0.0.0", port=8000)