from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Optional
from fastapi.testclient import TestClient

app = FastAPI()

class MissingSignal(BaseModel):
    signal_name: str
    reason: str

class InsufficientVerdictMCP(BaseModel):
    mcp_name: str
    server_id: str
    missing_signals: List[MissingSignal]

# Mock data for demonstration purposes
mcp_server_registry = [
    {"mcp_name": "MCP1", "server_id": "server1"},
    {"mcp_name": "MCP2", "server_id": "server2"},
    {"mcp_name": "MCP3", "server_id": "server3"},
]

mcp_signal_scores = [
    {"mcp_name": "MCP1", "signal_name": "signal1", "verdict": "INSUFFICIENT", "reason": "Signal not received"},
    {"mcp_name": "MCP1", "signal_name": "signal2", "verdict": "INSUFFICIENT", "reason": "Signal not received"},
    {"mcp_name": "MCP2", "signal_name": "signal1", "verdict": "SUFFICIENT", "reason": ""},
    {"mcp_name": "MCP3", "signal_name": "signal1", "verdict": "INSUFFICIENT", "reason": "Signal not received"},
]

@app.get("/api/v1/insufficient_verdicts", response_model=List[InsufficientVerdictMCP])
async def get_insufficient_verdicts(
    skip: Optional[int] = Query(0, ge=0),
    limit: Optional[int] = Query(100, ge=1, le=100)
):
    insufficient_mcps = []

    for mcp in mcp_server_registry:
        missing_signals = []
        for signal in mcp_signal_scores:
            if signal["mcp_name"] == mcp["mcp_name"] and signal["verdict"] == "INSUFFICIENT":
                missing_signals.append(MissingSignal(signal_name=signal["signal_name"], reason=signal["reason"]))

        if missing_signals:
            insufficient_mcps.append(InsufficientVerdictMCP(
                mcp_name=mcp["mcp_name"],
                server_id=mcp["server_id"],
                missing_signals=missing_signals
            ))

    return insufficient_mcps[skip:skip + limit]

if __name__ == "__main__":
    client = TestClient(app)
    response = client.get("/api/v1/insufficient_verdicts")
    assert response.status_code == 200
    assert response.json() == [
        {
            "mcp_name": "MCP1",
            "server_id": "server1",
            "missing_signals": [
                {"signal_name": "signal1", "reason": "Signal not received"},
                {"signal_name": "signal2", "reason": "Signal not received"}
            ]
        },
        {
            "mcp_name": "MCP3",
            "server_id": "server3",
            "missing_signals": [
                {"signal_name": "signal1", "reason": "Signal not received"}
            ]
        }
    ]