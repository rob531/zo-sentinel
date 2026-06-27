# comprehensive_mcp_risk_report_api.py

from fastapi import APIRouter, FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Callable, Awaitable
from fastapi.testclient import TestClient

# --- 1. Pydantic Models for Source API Responses ---

class McpServerVerdictResponse(BaseModel):
    """
    Represents the response structure from the mcp_server_verdict_api.
    """
    verdict: str = Field(..., description="Overall security verdict for the MCP server (e.g., 'SECURE', 'CRITICAL').")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score for the verdict, ranging from 0.0 to 1.0.")

class McpLlmAxisScoresResponse(BaseModel):
    """
    Represents the response structure from the mcp_llm_axis_scores_api.
    """
    axis_scores: Dict[str, float] = Field(
        ...,
        description="Scores for various LLM risk axes (e.g., 'data_exfiltration', 'prompt_injection', 'model_hallucination')."
    )
    overall_llm_risk: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Aggregated overall LLM risk score for the server, ranging from 0.0 to 1.0."
    )

class McpRiskRegisterEntry(BaseModel):
    """
    Represents a single entry in the MCP risk register.
    """
    risk_id: str = Field(..., description="Unique identifier for the registered risk.")
    description: str = Field(..., description="Detailed description of the risk.")
    severity: str = Field(..., description="Severity level of the risk (e.g., 'High', 'Medium', 'Low').")
    status: str = Field(..., description="Current status of the risk (e.g., 'Open', 'Mitigated', 'Closed').")
    # Additional fields can be added here as per actual risk register data

class McpRiskRegisterDashboardResponse(BaseModel):
    """
    Represents the response structure from the mcp_risk_register_dashboard_api.
    """
    registered_risks: List[McpRiskRegisterEntry] = Field(
        ...,
        description="A list of risks currently registered for the MCP server."
    )

# --- 2. Pydantic Model for the Combined Report ---

class ComprehensiveMcpRiskReport(BaseModel):
    """
    The comprehensive risk report combining data from multiple MCP APIs.
    """
    server_id: str = Field(..., description="The unique identifier of the MCP server.")
    verdict_report: McpServerVerdictResponse = Field(..., description="Overall security verdict and confidence for the server.")
    llm_risk_scores: McpLlmAxisScoresResponse = Field(..., description="Detailed LLM risk axis scores and an aggregated overall LLM risk.")
    registered_risks_dashboard: McpRiskRegisterDashboardResponse = Field(..., description="Dashboard view of all registered risks for the server.")

# --- 3. Type for the write_service client ---
# This Callable type represents an asynchronous function that takes a dictionary
# (the POST payload) and returns an awaitable dictionary (the JSON response).
WriteServiceClient = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]

# --- 4. FastAPI Router and Endpoint ---

router = APIRouter()

async def get_write_service_client() -> WriteServiceClient:
    """
    Dependency injector for the write_service client.
    In a production environment, this would return an actual HTTP client
    (e.g., an httpx.AsyncClient instance) configured to make POST requests
    to the `write_service` URL.

    For testing purposes, this dependency will be overridden to provide a mock.
    """
    # This placeholder ensures that if not overridden, an error is raised,
    # indicating that the dependency needs to be provided.
    raise NotImplementedError(
        "WriteServiceClient must be injected via dependency override "
        "for production use or testing."
    )

@router.get(
    "/mcps/{server_id}/risk_report",
    response_model=ComprehensiveMcpRiskReport,
    summary="Get comprehensive risk report for an MCP server",
    description="Aggregates security verdict, LLM risk scores, and registered risks for a given MCP server ID into a single comprehensive report."
)
async def get_comprehensive_mcp_risk_report(
    server_id: str,
    write_service_post: WriteServiceClient = Depends(get_write_service_client)
) -> ComprehensiveMcpRiskReport:
    """
    Retrieves a comprehensive risk report for a specified MCP server ID.

    This endpoint orchestrates calls to three internal APIs via the `write_service`:
    - `mcp_server_verdict_api`: For the overall security verdict.
    - `mcp_llm_axis_scores_api`: For detailed LLM-related risk scores.
    - `mcp_risk_register_dashboard_api`: For a list of all registered risks.

    The data from these sources is then combined into a single, structured JSON response.

    Args:
        server_id (str): The unique identifier of the MCP server.
        write_service_post (WriteServiceClient): Dependency-injected client for the write_service.

    Returns:
        ComprehensiveMcpRiskReport: A Pydantic model containing the aggregated risk data.

    Raises:
        HTTPException: If any of the underlying API calls fail or return unexpected data,
                       or if the server_id is not found by the mock service.
    """
    try:
        # 1. Call mcp_server_verdict_api
        verdict_payload = {"api_name": "mcp_server_verdict_api", "server_id": server_id}
        verdict_data = await write_service_post(verdict_payload)
        verdict_report = McpServerVerdictResponse(**verdict_data)

        # 2. Call mcp_llm_axis_scores_api
        llm_scores_payload = {"api_name": "mcp_llm_axis_scores_api", "server_id": server_id}
        llm_scores_data = await write_service_post(llm_scores_payload)
        llm_risk_scores = McpLlmAxisScoresResponse(**llm_scores_data)

        # 3. Call mcp_risk_register_dashboard_api
        risk_register_payload = {"api_name": "mcp_risk_register_dashboard_api", "server_id": server_id}
        risk_register_data = await write_service_post(risk_register_payload)
        registered_risks_dashboard = McpRiskRegisterDashboardResponse(**risk_register_data)

        # Combine all data into the comprehensive report
        return ComprehensiveMcpRiskReport(
            server_id=server_id,
            verdict_report=verdict_report,
            llm_risk_scores=llm_risk_scores,
            registered_risks_dashboard=registered_risks_dashboard
        )
    except HTTPException:
        # Re-raise HTTPExceptions that might come from the mock_write_service_post_implementation
        raise
    except Exception as e:
        # Catch any other unexpected errors during data fetching or parsing
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate comprehensive risk report for server '{server_id}': {e}"
        )

# --- 5. __main__ block for acceptance testing ---

if __name__ == "__main__":
    app = FastAPI(title="Comprehensive MCP Risk Report API")
    app.include_router(router)

    # Seeded in-memory store for the mock write_service
    # This dictionary simulates the data that the 'write_service' would return
    # for different API_names and server_ids.
    mock_db = {
        "mcp-server-1": {
            "mcp_server_verdict_api": {
                "verdict": "CRITICAL",
                "confidence": 0.95
            },
            "mcp_llm_axis_scores_api": {
                "axis_scores": {
                    "data_exfiltration": 0.8,
                    "prompt_injection": 0.7,
                    "model_hallucination": 0.5
                },
                "overall_llm_risk": 0.75
            },
            "mcp_risk_register_dashboard_api": {
                "registered_risks": [
                    {"risk_id": "RISK-001", "description": "Unpatched OS vulnerability", "severity": "High", "status": "Open"},
                    {"risk_id": "RISK-002", "description": "Weak IAM policy", "severity": "Medium", "status": "Open"},
                    {"risk_id": "RISK-003", "description": "Outdated container image", "severity": "High", "status": "Open"}
                ]
            }
        },
        "mcp-server-2": {
            "mcp_server_verdict_api": {
                "verdict": "SECURE",
                "confidence": 0.88
            },
            "mcp_llm_axis_scores_api": {
                "axis_scores": {
                    "data_exfiltration": 0.2,
                    "prompt_injection": 0.1,
                    "model_hallucination": 0.3
                },
                "overall_llm_risk": 0.2
            },
            "mcp_risk_register_dashboard_api": {
                "registered_risks": [] # No registered risks for this server
            }
        }
    }

    async def mock_write_service_post_implementation(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mock implementation of the write_service_post function for testing.
        It simulates fetching data from the in-memory `mock_db` based on the payload.
        """
        api_name = payload.get("api_name")
        server_id = payload.get("server_id")

        if not server_id or not api_name:
            raise HTTPException(status_code=400, detail="Missing 'server_id' or 'api_name' in write_service payload.")

        if server_id not in mock_db:
            raise HTTPException(status_code=404, detail=f"Server ID '{server_id}' not found in mock database.")

        if api_name not in mock_db[server_id]:
            # This case might happen if a specific API doesn't have data for a server,
            # but for this test, we assume all expected APIs have data if the server exists.
            # A real service might return an empty list or default values.
            raise HTTPException(status_code=404, detail=f"API '{api_name}' data not found for server ID '{server_id}' in mock database.")

        return mock_db[server_id][api_name]

    # Override the dependency for `get_write_service_client` to use our mock implementation
    app.dependency_overrides[get_write_service_client] = lambda: mock_write_service_post_implementation

    # Initialize FastAPI TestClient
    client = TestClient(app)

    print("Running acceptance tests for /mcps/{server_id}/risk_report...\n")

    # --- Test Case 1: Existing server with comprehensive data ---
    test_server_id_1 = "mcp-server-1"
    print(f"Testing server ID: {test_server_id_1}")
    response_1 = client.get(f"/mcps/{test_server_id_1}/risk_report")

    assert response_1.status_code == 200, \
        f"Test Case 1 Failed: Expected status 200, got {response_1.status_code}. Detail: {response_1.json().get('detail')}"
    report_1 = response_1.json()

    # Assertions for the combined report structure and data
    assert report_1["server_id"] == test_server_id_1
    assert report_1["verdict_report"]["verdict"] == "CRITICAL"
    assert report_1["verdict_report"]["confidence"] == 0.95
    assert report_1["llm_risk_scores"]["overall_llm_risk"] == 0.75
    assert "data_exfiltration" in report_1["llm_risk_scores"]["axis_scores"]
    assert report_1["llm_risk_scores"]["axis_scores"]["data_exfiltration"] == 0.8
    assert len(report_1["registered_risks_dashboard"]["registered_risks"]) == 3
    assert report_1["registered_risks_dashboard"]["registered_risks"][0]["risk_id"] == "RISK-001"
    assert report_1["registered_risks_dashboard"]["registered_risks"][2]["severity"] == "High"
    print(f"  Test Case 1 for '{test_server_id_1}' passed successfully.\n")

    # --- Test Case 2: Another existing server with different data (e.g., no registered risks) ---
    test_server_id_2 = "mcp-server-2"
    print(f"Testing server ID: {test_server_id_2}")
    response_2 = client.get(f"/mcps/{test_server_id_2}/risk_report")

    assert response_2.status_code == 200, \
        f"Test Case 2 Failed: Expected status 200, got {response_2.status_code}. Detail: {response_2.json().get('detail')}"
    report_2 = response_2.json()

    # Assertions for mcp-server-2
    assert report_2["server_id"] == test_server_id_2
    assert report_2["verdict_report"]["verdict"] == "SECURE"
    assert report_2["verdict_report"]["confidence"] == 0.88
    assert report_2["llm_risk_scores"]["overall_llm_risk"] == 0.2
    assert len(report_2["registered_risks_dashboard"]["registered_risks"]) == 0 # Expect no risks
    print(f"  Test Case 2 for '{test_server_id_2}' passed successfully.\n")

    # --- Test Case 3: Non-existent server ID ---
    test_server_id_non_existent = "mcp-server-999"
    print(f"Testing non-existent server ID: {test_server_id_non_existent}")
    response_non_existent = client.get(f"/mcps/{test_server_id_non_existent}/risk_report")

    assert response_non_existent.status_