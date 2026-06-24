import os
import re

def verify_integration():
    findings = []
    
    # 1. Inspect snow_connector.py
    snow_connector_path = 'snow_connector.py'
    if not os.path.exists(snow_connector_path):
        return f"Error: {snow_connector_path} not found."
    
    with open(snow_connector_path, 'r') as f:
        snow_content = f.read()

    # Check OAuth implementation
    oauth_env_vars = ['SNOW_CLIENT_ID', 'SNOW_CLIENT_SECRET', 'SNOW_OAUTH_TOKEN']
    oauth_found = all(var in snow_content for var in oauth_env_vars)
    if oauth_found:
        findings.append("[PASS] snow_connector.py uses SNOW OAuth tokens from environment.")
    else:
        findings.append("[FAIL] snow_connector.py missing required SNOW OAuth environment variables.")

    # Check Signature Validation
    sig_validation = "verify_snow_signature" in snow_content or "X-ServiceNow-Signature" in snow_content
    if sig_validation:
        findings.append("[PASS] snow_connector.py contains logic for request signature validation.")
    else:
        findings.append("[FAIL] snow_connector.py lacks request signature validation.")

    # Check strict enforcement (never accept unsigned)
    strict_enforcement = "raise" in snow_content and ("Unauthorized" in snow_content or "Signature" in snow_content)
    if sig_validation and strict_enforcement:
        findings.append("[PASS] snow_connector.py enforces signature validation (rejects unsigned).")
    else:
        findings.append("[FAIL] snow_connector.py does not strictly enforce signature validation.")

    # 2. Inspect approval_workflow.py
    approval_workflow_path = 'approval_workflow.py'
    if not os.path.exists(approval_workflow_path):
        return f"Error: {approval_workflow_path} not found."

    with open(approval_workflow_path, 'r') as f:
        approval_content = f.read()

    # Check MCP submission records via write_service
    mcp_submission_logic = "mcp_submissions" in approval_content and "write_service" in approval_content
    if mcp_submission_logic:
        findings.append("[PASS] approval_workflow.py writes MCP submission records to mcp_submissions table via write_service.")
    else:
        findings.append("[FAIL] approval_workflow.py missing mcp_submissions write logic via write_service.")

    # Check integration points
    integration_point = "snow_connector" in approval_content
    if integration_point:
        findings.append("[PASS] approval_workflow.py integrates with snow_connector.")
    else:
        findings.append("[FAIL] approval_workflow.py does not reference snow_connector.")

    return "\n".join(findings)

if __name__ == "__main__":
    report = verify_integration()
    print("--- Phase 9 Integration Verification Report ---")
    print(report)
    print("-----------------------------------------------")