from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import sqlite3

app = FastAPI()

# Mock database setup
def setup_mock_db():
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE mcp_server_registry (
            server_id INTEGER PRIMARY KEY,
            server_name TEXT,
            current_risk_tier TEXT,
            critical_signals TEXT
        )
    ''')
    mock_data = [
        (1, 'Server Alpha', 'High', 'CPU Usage, Memory Leak'),
        (2, 'Server Beta', 'Medium', 'Disk Space'),
        (3, 'Server Gamma', 'Low', 'None')
    ]
    cursor.executemany('INSERT INTO mcp_server_registry VALUES (?, ?, ?, ?)', mock_data)
    conn.commit()
    return conn

# Pydantic model for the response
class ServerRiskOverview(BaseModel):
    server_id: int
    server_name: str
    current_risk_tier: str
    critical_signals: List[str]

# API endpoint
@app.get("/server-risk-overview/", response_model=List[ServerRiskOverview])
async def get_server_risk_overview():
    conn = setup_mock_db()
    cursor = conn.cursor()
    cursor.execute('SELECT server_id, server_name, current_risk_tier, critical_signals FROM mcp_server_registry')
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No server data found")

    server_risk_overviews = []
    for row in rows:
        critical_signals = row[3].split(', ') if row[3] != 'None' else []
        server_risk_overviews.append({
            "server_id": row[0],
            "server_name": row[1],
            "current_risk_tier": row[2],
            "critical_signals": critical_signals
        })

    return server_risk_overviews

# Self-test
def test_get_server_risk_overview():
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/server-risk-overview/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["server_id"] == 1
    assert data[0]["server_name"] == "Server Alpha"
    assert data[0]["current_risk_tier"] == "High"
    assert data[0]["critical_signals"] == ["CPU Usage", "Memory Leak"]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)