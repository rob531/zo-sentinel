from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Dict, List, Optional
import psycopg2
from psycopg2 import sql

app = FastAPI()

# Pydantic models for schema definition
class Evidence(BaseModel):
    signal_type: str
    confidence: float
    details: str

class AxisDetail(BaseModel):
    score: float
    evidence: List[Evidence]

class RiskFactorsResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisDetail]
    overall_risk: float
    risk_tier: str

# Database connection setup
def get_db_connection():
    conn = psycopg2.connect(
        dbname="test_db",
        user="test_user",
        password="test_password",
        host="localhost"
    )
    return conn

# Helper function to seed the in-memory database
def seed_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
            server_id VARCHAR(255),
            axis_name VARCHAR(255),
            score FLOAT,
            PRIMARY KEY (server_id, axis_name)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id VARCHAR(255),
            axis_name VARCHAR(255),
            signal_type VARCHAR(255),
            confidence FLOAT,
            details TEXT,
            PRIMARY KEY (server_id, axis_name, signal_type)
        )
    """)

    # Insert test data
    cursor.execute("""
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, score)
        VALUES ('server1', 'axis1', 0.8), ('server1', 'axis2', 0.6),
               ('server1', 'axis3', 0.7), ('server1', 'axis4', 0.5),
               ('server1', 'axis5', 0.9), ('server1', 'axis6', 0.4)
    """)

    cursor.execute("""
        INSERT INTO mcp_signal_scores (server_id, axis_name, signal_type, confidence, details)
        VALUES ('server1', 'axis1', 'signal1', 0.9, 'Evidence for axis1'),
               ('server1', 'axis2', 'signal2', 0.7, 'Evidence for axis2'),
               ('server1', 'axis3', 'signal3', 0.8, 'Evidence for axis3'),
               ('server1', 'axis4', 'signal4', 0.6, 'Evidence for axis4'),
               ('server1', 'axis5', 'signal5', 0.9, 'Evidence for axis5'),
               ('server1', 'axis6', 'signal6', 0.5, 'Evidence for axis6')
    """)

    conn.commit()
    cursor.close()
    conn.close()

# API endpoint
@app.get("/servers/{server_id}/risk_factors", response_model=RiskFactorsResponse)
def get_risk_factors(server_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if server_id exists
    cursor.execute("SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE server_id = %s", (server_id,))
    if cursor.fetchone()[0] == 0:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    cursor.execute("""
        SELECT axis_name, score FROM mcp_llm_axis_scores WHERE server_id = %s
    """, (server_id,))
    axis_scores = cursor.fetchall()

    # Get evidence for each axis
    axes = {}
    for axis_name, score in axis_scores:
        cursor.execute("""
            SELECT signal_type, confidence, details FROM mcp_signal_scores
            WHERE server_id = %s AND axis_name = %s
        """, (server_id, axis_name))
        evidence_data = cursor.fetchall()
        evidence = [Evidence(signal_type=signal_type, confidence=confidence, details=details)
                   for signal_type, confidence, details in evidence_data]
        axes[axis_name] = AxisDetail(score=score, evidence=evidence)

    # Calculate overall risk and risk tier
    overall_risk = sum(axis.score for axis in axes.values()) / len(axes)
    if overall_risk >= 0.8:
        risk_tier = "High"
    elif overall_risk >= 0.5:
        risk_tier = "Medium"
    else:
        risk_tier = "Low"

    cursor.close()
    conn.close()

    return RiskFactorsResponse(
        server_id=server_id,
        axes=axes,
        overall_risk=overall_risk,
        risk_tier=risk_tier
    )

if __name__ == "__main__":
    seed_database()
    client = TestClient(app)

    # Test known server_id
    response = client.get("/servers/server1/risk_factors")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "server1"
    assert len(data["axes"]) == 6
    assert all(axis in data["axes"] for axis in ["axis1", "axis2", "axis3", "axis4", "axis5", "axis6"])
    assert data["overall_risk"] > 0
    assert data["risk_tier"] in ["High", "Medium", "Low"]

    # Test unknown server_id
    response = client.get("/servers/unknown/risk_factors")
    assert response.status_code == 404

    print("PASS")