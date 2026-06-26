from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from typing import Optional
import sqlite3
import json

app = FastAPI()

# Mock database for testing
def get_test_db():
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE service_health (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            last_heartbeat TEXT,
            status TEXT,
            meta TEXT
        )
    ''')
    cursor.execute('''
        INSERT INTO service_health (name, last_heartbeat, status, meta)
        VALUES ('attestation_refresher', '2023-01-01T00:00:00Z', 'running', '{"version": "1.0.0"}')
    ''')
    conn.commit()
    return conn

# Function to query the database
def query_db(query: str, params: tuple = ()):
    conn = sqlite3.connect('service_health.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = cursor.fetchone()
    conn.close()
    return result

@app.get("/attestation_refresher/status")
async def get_attestation_refresher_status():
    query = "SELECT name, last_heartbeat, status, meta FROM service_health WHERE name = ?"
    params = ('attestation_refresher',)
    result = query_db(query, params)

    if not result:
        raise HTTPException(status_code=404, detail="Attestation refresher status not found")

    name, last_heartbeat, status, meta = result
    meta_json = json.loads(meta) if meta else {}

    return {
        "name": name,
        "last_heartbeat": last_heartbeat,
        "status": status,
        "meta": meta_json
    }

# Self-test
def test_get_attestation_refresher_status():
    app.dependency_overrides[query_db] = get_test_db
    client = TestClient(app)
    response = client.get("/attestation_refresher/status")
    assert response.status_code == 200
    assert response.json() == {
        "name": "attestation_refresher",
        "last_heartbeat": "2023-01-01T00:00:00Z",
        "status": "running",
        "meta": {"version": "1.0.0"}
    }
    print("PASS")

if __name__ == "__main__":
    test_get_attestation_refresher_status()