import json
import os
import unittest
from typing import List, Optional, Dict, Any, Literal
from unittest.mock import patch, MagicMock

import requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

# --- Configuration ---
# In a real application, this would be loaded from environment variables or a config file.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8001")

# --- FastAPI Application ---
app = FastAPI(
    title="MCP Search Filter API",
    description="Provides filtered and sorted MCP search results.",
    version="1.0.0",
)

# Define allowed values for sorting
SortBy = Literal["mcp_name", "overall_risk"]
SortOrder = Literal["asc", "desc"]

class MCPDetail(BaseModel):
    mcp_name: str
    overall_risk: float
    risk_tier: str

@app.get(
    "/mcp/search/filtered",
    response_model=List[MCPDetail],
    summary="Get filtered and sorted MCP search results",
    description="""
    Retrieves a list of MCP servers, filtered by risk tier, signal score range,
    and sorted by specified criteria.
    """,
    response_description="A JSON array of filtered and sorted MCP server details."
)
async def get_filtered_mcp_search(
    risk_tier: Optional[str] = Query(
        None,
        description="Filter by risk tier (e.g., 'HIGH_RISK_ISOLATED', 'LOW_RISK_MONITORED')."
    ),
    signal_score_min: Optional[float] = Query(
        None,
        ge=0.0,
        le=100.0,
        description="Minimum signal score (0-100)."
    ),
    signal_score_max: Optional[float] = Query(
        None,
        ge=0.0,
        le=100.0,
        description="Maximum signal score (0-100)."
    ),
    sort_by: SortBy = Query(
        "mcp_name",
        description="Field to sort by ('mcp_name' or 'overall_risk')."
    ),
    sort_order: SortOrder = Query(
        "asc",
        description="Sort order ('asc' for ascending, 'desc' for descending)."
    ),
) -> List[MCPDetail]:
    """
    Constructs a SQL query based on provided filters and sorting parameters,
    then queries the `write_service` to retrieve the results.
    """
    sql_query_parts = [
        "SELECT s.mcp_name, s.overall_risk, s.risk_tier",
        "FROM mcp_server_registry s",
        "JOIN mcp_llm_axis_scores a ON s.mcp_name = a.mcp_name",
        "WHERE 1=1"  # Always true, simplifies appending AND clauses
    ]
    params: Dict[str, Any] = {}

    if risk_tier:
        sql_query_parts.append("AND s.risk_tier = :risk_tier")
        params["risk_tier"] = risk_tier

    if signal_score_min is not None:
        sql_query_parts.append("AND a.signal_score >= :signal_score_min")
        params["signal_score_min"] = signal_score_min

    if signal_score_max is not None:
        sql_query_parts.append("AND a.signal_score <= :signal_score_max")
        params["signal_score_max"] = signal_score_max

    # Add sorting
    sql_query_parts.append(f"ORDER BY s.{sort_by} {sort_order.upper()}")

    full_sql_query = " ".join(sql_query_parts)

    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": full_sql_query, "params": params},
            timeout=10
        )
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        db_results = response.json()

        # Ensure results are in the expected format
        if not isinstance(db_results, list):
            raise ValueError("Unexpected response format from write_service: not a list.")

        # Map results to MCPDetail model
        filtered_mcp_details = [
            MCPDetail(
                mcp_name=row.get("mcp_name", "UNKNOWN"),
                overall_risk=row.get("overall_risk", 0.0),
                risk_tier=row.get("risk_tier", "UNKNOWN")
            )
            for row in db_results
        ]
        return filtered_mcp_details

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to database service: {e}"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing database response: {e}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {e}"
        )

# --- Acceptance Tests ---
if __name__ == "__main__":
    client = TestClient(app)

    # Mock database data for testing
    MOCK_DB_DATA = [
        {"mcp_name": "server_alpha", "overall_risk": 0.8, "risk_tier": "HIGH_RISK_ISOLATED", "signal_score": 95.0},
        {"mcp_name": "server_beta", "overall_risk": 0.3, "risk_tier": "LOW_RISK_MONITORED", "signal_score": 70.0},
        {"mcp_name": "server_gamma", "overall_risk": 0.6, "risk_tier": "MEDIUM_RISK_REVIEW", "signal_score": 80.0},
        {"mcp_name": "server_delta", "overall_risk": 0.9, "risk_tier": "HIGH_RISK_ISOLATED", "signal_score": 98.0},
        {"mcp_name": "server_epsilon", "overall_risk": 0.2, "risk_tier": "LOW_RISK_MONITORED", "signal_score": 65.0},
        {"mcp_name": "server_zeta", "overall_risk": 0.5, "risk_tier": "MEDIUM_RISK_REVIEW", "signal_score": 75.0},
    ]

    def mock_db_query(sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Simulates database filtering and sorting based on SQL and params.
        This is a simplified mock and doesn't fully parse SQL, but applies
        filters and sorting to the MOCK_DB_DATA.
        """
        results = MOCK_DB_DATA[:] # Start with a copy of all data

        # Apply filters
        if "risk_tier" in params:
            results = [r for r in results if r["risk_tier"] == params["risk_tier"]]
        if "signal_score_min" in params:
            results = [r for r in results if r["signal_score"] >= params["signal_score_min"]]
        if "signal_score_max" in params:
            results = [r for r in results if r["signal_score"] <= params["signal_score_max"]]

        # Determine sort_by and sort_order from SQL string (simplified parsing)
        sort_by_field = "mcp_name" # Default
        sort_order_dir = "asc" # Default

        if "ORDER BY" in sql:
            order_clause = sql.split("ORDER BY")[1].strip()
            parts = order_clause.split(" ")
            if len(parts) >= 2:
                # Extract field, assuming it's prefixed with 's.'
                field_name = parts[0].replace("s.", "")
                if field_name in ["mcp_name", "overall_risk"]:
                    sort_by_field = field_name
                if parts[1].lower() in ["asc", "desc"]:
                    sort_order_dir = parts[1].lower()

        # Apply sorting
        reverse = (sort_order_dir == "desc")
        results.sort(key=lambda x: x[sort_by_field], reverse=reverse)

        # Return only the requested fields
        return [{k: r[k] for k in ["mcp_name", "overall_risk", "risk_tier"]} for r in results]

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code
            self.ok = status_code < 400

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"HTTP Error: {self.status_code}")

    test_results = []

    # Patch requests.post for the duration of the tests
    with patch("requests.post") as mock_post:
        def side_effect_func(url, json, timeout):
            if url == f"{WRITE_SERVICE_URL}/query":
                sql = json["sql"]
                params = json["params"]
                mocked_data = mock_db_query(sql, params)
                return MockResponse(mocked_data)
            return MockResponse({"error": "Not Found"}, 404) # Default for other URLs

        mock_post.side_effect = side_effect_func

        print("Running acceptance tests...")

        # Test Case 1: No filters, default sort (mcp_name asc)
        response = client.get("/mcp/search/filtered")
        assert response.status_code == 200, f"Test 1 Failed: {response.status_code} - {response.text}"
        data = response.json()
        expected_data = [
            {"mcp_name": "server_alpha", "overall_risk": 0.8, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_beta", "overall_risk": 0.3, "risk_tier": "LOW_RISK_MONITORED"},
            {"mcp_name": "server_delta", "overall_risk": 0.9, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_epsilon", "overall_risk": 0.2, "risk_tier": "LOW_RISK_MONITORED"},
            {"mcp_name": "server_gamma", "overall_risk": 0.6, "risk_tier": "MEDIUM_RISK_REVIEW"},
            {"mcp_name": "server_zeta", "overall_risk": 0.5, "risk_tier": "MEDIUM_RISK_REVIEW"},
        ]
        assert data == expected_data, f"Test 1 Failed: {data}"
        test_results.append(True)
        print("Test 1: No filters, default sort - PASS")

        # Test Case 2: Filter by risk_tier=HIGH_RISK_ISOLATED
        response = client.get("/mcp/search/filtered?risk_tier=HIGH_RISK_ISOLATED")
        assert response.status_code == 200, f"Test 2 Failed: {response.status_code} - {response.text}"
        data = response.json()
        expected_data = [
            {"mcp_name": "server_alpha", "overall_risk": 0.8, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_delta", "overall_risk": 0.9, "risk_tier": "HIGH_RISK_ISOLATED"},
        ]
        assert data == expected_data, f"Test 2 Failed: {data}"
        test_results.append(True)
        print("Test 2: Filter by risk_tier - PASS")

        # Test Case 3: Sort by overall_risk desc
        response = client.get("/mcp/search/filtered?sort_by=overall_risk&sort_order=desc")
        assert response.status_code == 200, f"Test 3 Failed: {response.status_code} - {response.text}"
        data = response.json()
        expected_data = [
            {"mcp_name": "server_delta", "overall_risk": 0.9, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_alpha", "overall_risk": 0.8, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_gamma", "overall_risk": 0.6, "risk_tier": "MEDIUM_RISK_REVIEW"},
            {"mcp_name": "server_zeta", "overall_risk": 0.5, "risk_tier": "MEDIUM_RISK_REVIEW"},
            {"mcp_name": "server_beta", "overall_risk": 0.3, "risk_tier": "LOW_RISK_MONITORED"},
            {"mcp_name": "server_epsilon", "overall_risk": 0.2, "risk_tier": "LOW_RISK_MONITORED"},
        ]
        assert data == expected_data, f"Test 3 Failed: {data}"
        test_results.append(True)
        print("Test 3: Sort by overall_risk desc - PASS")

        # Test Case 4: Combined filters: risk_tier=HIGH_RISK_ISOLATED, sort_by=overall_risk&sort_order=desc
        response = client.get("/mcp/search/filtered?risk_tier=HIGH_RISK_ISOLATED&sort_by=overall_risk&sort_order=desc")
        assert response.status_code == 200, f"Test 4 Failed: {response.status_code} - {response.text}"
        data = response.json()
        expected_data = [
            {"mcp_name": "server_delta", "overall_risk": 0.9, "risk_tier": "HIGH_RISK_ISOLATED"},
            {"mcp_name": "server_alpha", "overall_risk": 0.8, "risk_tier": "HIGH_RISK_ISOLATED"},
        ]
        assert data == expected_data, f"Test 4 Failed: {data}"
        test_results.append(True)
        print("Test 4: Combined filters - PASS")

        # Test Case 5: Filter by signal_score_min and signal_score_max
        response = client.get("/mcp/search/filtered?signal_score_min=70&signal_score_max=80")
        assert response.status_code == 200, f"Test 5 Failed: {response.status_code} - {response.text}"
        data = response.json()
        expected_data = [
            {"mcp_name": "server_beta", "overall_risk": 0.3, "risk_tier": "LOW_RISK_MONITORED"},
            {"mcp_name": "server_gamma", "overall_risk": 0.6, "risk_tier": "MEDIUM_RISK_REVIEW"},
            {"mcp_name": "server_zeta", "overall_risk": 0.5, "risk_tier": "MEDIUM_RISK_REVIEW"},
        ]
        assert data == expected_data, f"Test 5 Failed: {data}"
        test_results.append(True)
        print("Test 5: Filter by signal_score range - PASS")

        # Test Case 6: Empty results (non-existent risk_tier)
        response = client.get("/mcp/search/filtered?risk_tier=NON_EXISTENT_RISK")
        assert response.status_code == 200, f"Test 6 Failed: {response.status_code} - {response.text}"
        data = response.json()
        assert data == [], f"Test 6 Failed: {data}"
        test_results.append(True)
        print("Test 6: Empty results - PASS")

        # Test Case 7: Invalid sort_by parameter (FastAPI validation)
        response = client.get("/mcp/search/filtered?sort_by=invalid_field")
        assert response.status_code == 422, f"Test 7 Failed: Expected 422, got {response.status_code} - {response.text}"
        assert "value is not a valid enumeration member" in response.json()["detail"][0]["msg"]
        test_results.append(True)
        print("Test 7: Invalid sort_by parameter - PASS")

        # Test Case 8: Invalid sort_order parameter (FastAPI validation)
        response = client.get("/mcp/search/filtered?sort_order=up")
        assert response.status_code == 422, f"Test 8 Failed: Expected 422, got {response.status_code} - {response.text}"
        assert "value is not a valid enumeration member" in response.json()["detail"][0]["msg"]
        test_results.append(True)
        print("Test 8: Invalid sort_order parameter - PASS")

        # Test Case 9: Invalid signal_score_min (out of range)
        response = client.get("/mcp/search/filtered?signal_score_min=101")
        assert response.status_code == 422, f"Test 9 Failed: Expected 422, got {response.status_code} - {response.text}"
        assert "ensure this value is less than or equal to 100" in response.json()["detail"][0]["msg"]
        test_results.append(True)
        print("Test 9: Invalid signal_score_min - PASS")

        # Test Case 10: write_service returns an error (mocking 500 from write_service)
        mock_post.side_effect = lambda url, json, timeout: MockResponse({"error": "DB connection failed"}, 500)
        response = client.get("/mcp/search/filtered")
        assert response.status_code == 500, f"Test 10 Failed: Expected 500, got {response.status_code} - {response.text}"
        assert "Failed to connect to database service" in response.json()["detail"]
        test_results.append(True)
        print("Test 10: write_service error - PASS")

        # Reset mock for final check
        mock_post.side_effect = side_effect_func

    if all(test_results):
        print("\nAll acceptance tests PASSED!")
    else:
        print("\nSome acceptance tests FAILED!")