import pytest
from fastapi.testclient import TestClient
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from app.main import app
from mcp_server_risk_export_generator import export_mcp_server_risk_data
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from io import StringIO
import csv
import os

@pytest.fixture
def test_client():
    return TestClient(app)

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    app.dependency_overrides[get_session] = lambda: db
    return db

def test_export_mcp_server_risk_data_basic(test_db):
    # Setup test data
    org = Org(name="Test Org")
    user = User(email="test@example.com", org=org)
    server = MCPServerRegistry(
        hostname="test.example.com",
        ip_address="192.168.1.1",
        org=org,
        owner=user
    )
    score = MCPLLMAxisScores(
        server=server,
        axis="security",
        score=0.8,
        timestamp="2023-01-01T00:00:00"
    )
    test_db.add_all([org, user, server, score])
    test_db.commit()

    # Test export
    output_file = "test_export.csv"
    export_mcp_server_risk_data(output_file)

    # Verify output
    with open(output_file, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)

    assert len(rows) == 2  # header + 1 data row
    assert rows[0] == [
        "hostname", "ip_address", "org_name", "owner_email",
        "security_score", "security_timestamp"
    ]
    assert rows[1][0] == "test.example.com"
    assert rows[1][1] == "192.168.1.1"
    assert rows[1][2] == "Test Org"
    assert rows[1][3] == "test@example.com"
    assert rows[1][4] == "0.8"
    assert rows[1][5] == "2023-01-01T00:00:00"

    os.remove(output_file)

def test_export_mcp_server_risk_data_multiple_scores(test_db):
    # Setup test data
    org = Org(name="Test Org")
    user = User(email="test@example.com", org=org)
    server = MCPServerRegistry(
        hostname="test.example.com",
        ip_address="192.168.1.1",
        org=org,
        owner=user
    )
    scores = [
        MCPLLMAxisScores(
            server=server,
            axis="security",
            score=0.8,
            timestamp="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server=server,
            axis="performance",
            score=0.9,
            timestamp="2023-01-02T00:00:00"
        )
    ]
    test_db.add_all([org, user, server] + scores)
    test_db.commit()

    # Test export
    output_file = "test_export.csv"
    export_mcp_server_risk_data(output_file)

    # Verify output
    with open(output_file, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)

    assert len(rows) == 2  # header + 1 data row (only security score is exported)
    assert rows[0] == [
        "hostname", "ip_address", "org_name", "owner_email",
        "security_score", "security_timestamp"
    ]
    assert rows[1][0] == "test.example.com"
    assert rows[1][1] == "192.168.1.1"
    assert rows[1][2] == "Test Org"
    assert rows[1][3] == "test@example.com"
    assert rows[1][4] == "0.8"
    assert rows[1][5] == "2023-01-01T00:00:00"

    os.remove(output_file)

def test_export_mcp_server_risk_data_no_scores(test_db):
    # Setup test data
    org = Org(name="Test Org")
    user = User(email="test@example.com", org=org)
    server = MCPServerRegistry(
        hostname="test.example.com",
        ip_address="192.168.1.1",
        org=org,
        owner=user
    )
    test_db.add_all([org, user, server])
    test_db.commit()

    # Test export
    output_file = "test_export.csv"
    export_mcp_server_risk_data(output_file)

    # Verify output
    with open(output_file, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)

    assert len(rows) == 1  # only header
    assert rows[0] == [
        "hostname", "ip_address", "org_name", "owner_email",
        "security_score", "security_timestamp"
    ]

    os.remove(output_file)

if __name__ == "__main__":
    import sys
    from pytest import main

    result = main([__file__])
    if result == 0:
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)