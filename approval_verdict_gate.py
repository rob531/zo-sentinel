#!/usr/bin/env python3
"""
Approval Verdict Gate Utility

FastAPI endpoint acting as a policy gate that blocks deployment of MCPs
below a configurable trust threshold. Validates incoming deployment requests
against mcp_risk_register and mcp_decisions tables via write_service.
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, asdict

from fastapi import FastAPI, HTTPException, Header, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment
GATE_MIN_SCORE = float(os.environ.get('GATE_MIN_SCORE', '45'))
GATE_OVERRIDE_REQUIRE_REASON = os.environ.get('GATE_OVERRIDE_REQUIRE_REASON', 'true').lower() == 'true'
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://127.0.0.1:8772')

# Verdict tier definitions (score thresholds)
VERDICT_TIERS = {
    'TRUSTED_GENERAL': 75,
    'TRUSTED_RESEARCH': 60,
    'ENTERPRISE_CONTROLLED': 45,
    'CAUTION_LIMITED': 30,
    'HIGH_RISK_ISOLATED': 15,
    'KNOWN_THREAT': 0,
}

# Reverse lookup: score -> verdict tier
def get_verdict_tier(score: float) -> str:
    """Determine verdict tier based on composite score."""
    if score >= 75:
        return 'TRUSTED_GENERAL'
    elif score >= 60:
        return 'TRUSTED_RESEARCH'
    elif score >= 45:
        return 'ENTERPRISE_CONTROLLED'
    elif score >= 30:
        return 'CAUTION_LIMITED'
    elif score >= 15:
        return 'HIGH_RISK_ISOLATED'
    else:
        return 'KNOWN_THREAT'


@dataclass
class DeployGateResponse:
    """Response model for deployment gate check."""
    allowed: bool
    verdict: str
    composite_score: float
    blocking_signals: list[str]
    requires_override: bool


@dataclass
class OverrideResponse:
    """Response model for override action."""
    success: bool
    override_logged: bool


class OverrideRequest(BaseModel):
    """Request model for override action."""
    mcp_name: str = Field(..., description="Name of the MCP to override")
    reason: Optional[str] = Field(None, description="Reason for override (required if GATE_OVERRIDE_REQUIRE_REASON=true)")
    analyst_guid: Optional[str] = Field(None, description="Analyst GUID for the override")


# Initialize FastAPI app
app = FastAPI(
    title="Approval Verdict Gate",
    description="Policy gate for MCP deployment requests that validates against ZO-SENTINEL verdicts",
    version="1.0.0"
)


def _make_write_service_request(method: str, endpoint: str, data: Optional[dict] = None) -> dict:
    """
    Make a request to the write_service.
    
    All database access goes through write_service at 127.0.0.1:8772.
    """
    url = f"{WRITE_SERVICE_URL}{endpoint}"
    headers = {'Content-Type': 'application/json'}
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=data, timeout=10)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=10)
        elif method.upper() == 'PUT':
            response = requests.put(url, headers=headers, json=data, timeout=10)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        response.raise_for_status()
        return response.json() if response.content else {}
    
    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to write_service at {url}")
        raise HTTPException(status_code=503, detail="Write service unavailable (timeout)")
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error connecting to write_service at {url}")
        raise HTTPException(status_code=503, detail="Write service unavailable")
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error from write_service: {e}")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error calling write_service: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


def query_mcp_risk_register(mcp_name: str) -> Optional[dict]:
    """
    Query mcp_risk_register for composite_score and dominant_verdict.
    """
    try:
        result = _make_write_service_request(
            'POST',
            '/query',
            {
                'table': 'mcp_risk_register',
                'filters': {'mcp_name': mcp_name},
                'columns': ['mcp_name', 'composite_score', 'dominant_verdict', 'last_updated']
            }
        )
        rows = result.get('rows', [])
        return rows[0] if rows else None
    except HTTPException:
        # Fallback: try as direct SQL query endpoint
        try:
            result = _make_write_service_request(
                'GET',
                '/mcp_risk_register',
                {'mcp_name': mcp_name}
            )
            return result
        except HTTPException:
            return None


def query_mcp_signal_scores(mcp_name: str) -> list[dict]:
    """
    Query mcp_signal_scores for per-signal breakdown.
    """
    try:
        result = _make_write_service_request(
            'POST',
            '/query',
            {
                'table': 'mcp_signal_scores',
                'filters': {'mcp_name': mcp_name},
                'columns': ['signal_name', 'score', 'threshold', 'passed']
            }
        )
        return result.get('rows', [])
    except HTTPException:
        # Fallback: try as direct endpoint
        try:
            result = _make_write_service_request(
                'GET',
                '/mcp_signal_scores',
                {'mcp_name': mcp_name}
            )
            return result if isinstance(result, list) else result.get('rows', [])
        except HTTPException:
            return []


def insert_audit_log(entry: dict) -> bool:
    """
    Insert an audit_log entry for a gate decision.
    """
    try:
        _make_write_service_request(
            'POST',
            '/insert',
            {
                'table': 'audit_log',
                'data': entry
            }
        )
        return True
    except HTTPException:
        logger.error("Failed to insert audit log entry")
        return False


def insert_mcp_decision(decision: dict) -> bool:
    """
    Insert a mcp_decisions entry for an override action.
    """
    try:
        _make_write_service_request(
            'POST',
            '/insert',
            {
                'table': 'mcp_decisions',
                'data': decision
            }
        )
        return True
    except HTTPException:
        logger.error("Failed to insert mcp_decisions entry")
        return False


def create_audit_entry(
    mcp_name: str,
    analyst_guid: Optional[str],
    decision: str,
    composite_score: float,
    verdict: str,
    blocking_signals: list[str],
    override_used: bool = False
) -> dict:
    """
    Create an audit log entry.
    """
    return {
        'event_type': 'DEPLOYMENT_GATE_DECISION',
        'mcp_name': mcp_name,
        'analyst_guid': analyst_guid or 'SYSTEM',
        'decision': decision,  # ALLOWED, DENIED, OVERRIDE
        'composite_score': composite_score,
        'dominant_verdict': verdict,
        'blocking_signals': json.dumps(blocking_signals),
        'min_threshold': GATE_MIN_SCORE,
        'override_used': override_used,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event_id': str(uuid.uuid4())
    }


@app.get('/gate/deploy/{mcp_name}', response_model=DeployGateResponse)
def check_deploy_gate(
    mcp_name: str = Path(..., description="Name of the MCP to check for deployment"),
    authorization: Optional[str] = Header(None, description="Authorization header with analyst GUID")
) -> DeployGateResponse:
    """
    Check if an MCP is allowed to be deployed based on its trust score.
    
    Returns:
        - allowed: Whether deployment is permitted
        - verdict: The verdict tier for this MCP
        - composite_score: The calculated trust score
        - blocking_signals: List of signals that are below threshold (if blocked)
        - requires_override: Whether an override is needed to proceed
    """
    analyst_guid = None
    if authorization:
        # Extract GUID from Bearer token or raw value
        if authorization.startswith('Bearer '):
            analyst_guid = authorization[7:]
        else:
            analyst_guid = authorization
    
    logger.info(f"Gate check requested for MCP: {mcp_name} by analyst: {analyst_guid or 'SYSTEM'}")
    
    # Query risk register
    risk_data = query_mcp_risk_register(mcp_name)
    
    if risk_data is None:
        logger.warning(f"MCP not found in risk register: {mcp_name}")
        # MCP not found - treat as high risk, require override
        response = DeployGateResponse(
            allowed=False,
            verdict='UNKNOWN',
            composite_score=0.0,
            blocking_signals=['MCP_NOT_IN_REGISTRY'],
            requires_override=True
        )
    else:
        composite_score = float(risk_data.get('composite_score', 0))
        dominant_verdict = risk_data.get('dominant_verdict', 'UNKNOWN')
        
        # Determine verdict tier
        verdict = dominant_verdict if dominant_verdict in VERDICT_TIERS else get_verdict_tier(composite_score)
        
        # Check if score meets threshold
        meets_threshold = composite_score >= GATE_MIN_SCORE
        
        # Get blocking signals
        blocking_signals = []
        if not meets_threshold:
            signal_scores = query_mcp_signal_scores(mcp_name)
            for sig in signal_scores:
                if not sig.get('passed', True):
                    threshold = sig.get('threshold', 0)
                    score = sig.get('score', 0)
                    blocking_signals.append(
                        f"{sig.get('signal_name', 'UNKNOWN')}: score={score}, threshold={threshold}"
                    )
            
            # If no signal data, add general threshold failure
            if not blocking_signals:
                blocking_signals.append(f'composite_score below threshold: {composite_score} < {GATE_MIN_SCORE}')
        
        response = DeployGateResponse(
            allowed=meets_threshold,
            verdict=verdict,
            composite_score=composite_score,
            blocking_signals=blocking_signals,
            requires_override=not meets_threshold
        )
    
    # Log the gate decision
    audit_entry = create_audit_entry(
        mcp_name=mcp_name,
        analyst_guid=analyst_guid,
        decision='ALLOWED' if response.allowed else 'DENIED',
        composite_score=response.composite_score,
        verdict=response.verdict,
        blocking_signals=response.blocking_signals,
        override_used=False
    )
    insert_audit_log(audit_entry)
    
    logger.info(
        f"Gate decision for {mcp_name}: allowed={response.allowed}, "
        f"verdict={response.verdict}, score={response.composite_score}"
    )
    
    return response


@app.post('/gate/override', response_model=OverrideResponse)
def submit_override(
    request: OverrideRequest,
    authorization: Optional[str] = Header(None, description="Authorization header with analyst GUID")
) -> OverrideResponse:
    """
    Submit an override for a blocked MCP deployment.
    
    Args:
        request: Override request containing mcp_name and optional reason
        authorization: Authorization header with analyst GUID
    
    Returns:
        - success: Whether the override was processed
        - override_logged: Whether the override was logged to the database
    """
    analyst_guid = request.analyst_guid
    if not analyst_guid and authorization:
        if authorization.startswith('Bearer '):
            analyst_guid = authorization[7:]
        else:
            analyst_guid = authorization
    
    # Validate reason requirement
    if GATE_OVERRIDE_REQUIRE_REASON and not request.reason:
        raise HTTPException(
            status_code=400,
            detail="Override reason is required. Set GATE_OVERRIDE_REQUIRE_REASON=false to disable."
        )
    
    logger.info(f"Override requested for MCP: {request.mcp_name} by analyst: {analyst_guid}")
    
    # Create decision record
    decision_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    
    decision_record = {
        'decision_id': decision_id,
        'mcp_name': request.mcp_name,
        'decision_type': 'OVERRIDE',
        'analyst_guid': analyst_guid or 'UNKNOWN',
        'reason': request.reason or 'No reason provided',
        'timestamp': timestamp,
        'gate_min_score': GATE_MIN_SCORE,
        'status': 'APPLIED'
    }
    
    # Insert decision record
    override_logged = insert_mcp_decision(decision_record)
    
    # Log the override in audit log
    audit_entry = create_audit_entry(
        mcp_name=request.mcp_name,
        analyst_guid=analyst_guid,
        decision='OVERRIDE',
        composite_score=0.0,  # May want to fetch actual score
        verdict='OVERRIDE_APPLIED',
        blocking_signals=[],
        override_used=True
    )
    audit_entry['override_decision_id'] = decision_id
    audit_entry['override_reason'] = request.reason
    insert_audit_log(audit_entry)
    
    logger.info(f"Override processed for {request.mcp_name}: logged={override_logged}")
    
    return OverrideResponse(
        success=True,
        override_logged=override_logged
    )


@app.get('/health')
def health_check():
    """Health check endpoint."""
    return {'status': 'healthy', 'service': 'approval_verdict_gate'}


@app.get('/gate/config')
def get_gate_config():
    """Get current gate configuration."""
    return {
        'min_score_threshold': GATE_MIN_SCORE,
        'override_require_reason': GATE_OVERRIDE_REQUIRE_REASON,
        'write_service_url': WRITE_SERVICE_URL,
        'verdict_tiers': VERDICT_TIERS
    }


if __name__ == '__main__':
    import sys
    
    # Smoke test: verify the module imports cleanly
    print("Testing module import...")
    import approval_verdict_gate
    assert hasattr(approval_verdict_gate, 'app'), "app attribute not found"
    
    # Verify routes are registered
    routes = [r.path for r in approval_verdict_gate.app.routes]
    print(f"Registered routes: {routes}")
    
    assert '/gate/deploy/{mcp_name}' in routes, f"Route /gate/deploy/{{mcp_name}} not found. Found: {routes}"
    assert '/gate/override' in routes, f"Route /gate/override not found. Found: {routes}"
    
    # Verify config
    assert hasattr(approval_verdict_gate, 'GATE_MIN_SCORE'), "GATE_MIN_SCORE not found"
    assert approval_verdict_gate.GATE_MIN_SCORE == 45.0, f"Expected default GATE_MIN_SCORE of 45, got {approval_verdict_gate.GATE_MIN_SCORE}"
    
    # Verify verdict tiers
    assert 'TRUSTED_GENERAL' in approval_verdict_gate.VERDICT_TIERS
    assert 'ENTERPRISE_CONTROLLED' in approval_verdict_gate.VERDICT_TIERS
    assert 'KNOWN_THREAT' in approval_verdict_gate.VERDICT_TIERS
    
    # Verify helper function
    assert approval_verdict_gate.get_verdict_tier(80) == 'TRUSTED_GENERAL'
    assert approval_verdict_gate.get_verdict_tier(50) == 'ENTERPRISE_CONTROLLED'
    assert approval_verdict_gate.get_verdict_tier(10) == 'KNOWN_THREAT'
    
    print("PASS: approval_verdict_gate imports and routes registered")
    print(f"  - Min score threshold: {approval_verdict_gate.GATE_MIN_SCORE}")
    print(f"  - Verdict tiers: {list(approval_verdict_gate.VERDICT_TIERS.keys())}")
    sys.exit(0)