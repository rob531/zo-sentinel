#!/usr/bin/env python3
"""
Rug Pull Monitor Daemon

Monitors MCP package unpublishes/ownership changes in real time.
Compares SHA/checksum of tool schemas vs stored mcp_fingerprints.
Writes HIGH_RISK_ISOLATED verdict + mcp_threat_associations row for flagged servers.
"""

import json
import logging
import sys
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('rug_pull_monitor')

# Configuration
CYCLE_INTERVAL = 300  # seconds
REGISTRY_TIMEOUT = 10  # seconds per registry check

# Service endpoints
WRITE_SERVICE_URL = 'http://localhost:8080/write'
SERVICE_HEALTH_URL = 'http://localhost:8080/service_health'
REGISTRY_URL = 'http://localhost:8081/registry'


def send_heartbeat() -> bool:
    """Send heartbeat to service_health table."""
    try:
        response = requests.post(
            SERVICE_HEALTH_URL,
            json={
                'service': 'rug_pull_monitor',
                'status': 'running',
                'timestamp': datetime.utcnow().isoformat()
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info("Heartbeat sent to service_health")
        return True
    except requests.RequestException as e:
        logger.warning(f"Failed to send heartbeat: {e}")
        return False


def fetch_registry_metadata(limit: int = 500) -> List[Dict[str, Any]]:
    """Fetch registry metadata for known MCPs (last 500 by first_seen desc)."""
    try:
        response = requests.get(
            f"{REGISTRY_URL}/mcp_server_registry",
            params={'limit': limit},
            timeout=REGISTRY_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return data.get('servers', [])
    except requests.RequestException as e:
        logger.error(f"Failed to fetch registry metadata: {e}")
        return []


def fetch_fingerprints(mcp_names: List[str]) -> Dict[str, str]:
    """Fetch stored fingerprints for given MCP names."""
    fingerprints = {}
    try:
        response = requests.get(
            f"{REGISTRY_URL}/mcp_fingerprints",
            json={'names': mcp_names},
            timeout=REGISTRY_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        fingerprints = data.get('fingerprints', {})
    except requests.RequestException as e:
        logger.error(f"Failed to fetch fingerprints: {e}")
    return fingerprints


def detect_rug_pull(mcp: Dict[str, Any], stored_fingerprint: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Detect rug pull indicators for an MCP.
    
    Returns a threat dict if discrepancy found, None otherwise.
    """
    mcp_name = mcp.get('name', 'unknown')
    current_checksum = mcp.get('checksum') or mcp.get('sha256')
    current_published = mcp.get('published', True)
    current_owner = mcp.get('owner')
    current_version = mcp.get('version')
    
    threat = None
    
    # Check checksum/fingerprint discrepancy
    if stored_fingerprint and current_checksum:
        if stored_fingerprint != current_checksum:
            threat = {
                'type': 'CHECKSUM_MISMATCH',
                'severity': 'HIGH',
                'mcp_name': mcp_name,
                'stored_fingerprint': stored_fingerprint,
                'current_checksum': current_checksum,
                'details': f"Fingerprint mismatch: stored={stored_fingerprint[:16]}..., current={current_checksum[:16]}..."
            }
    
    # Check if package was unpublished
    if not current_published:
        threat = {
            'type': 'UNPUBLISHED',
            'severity': 'HIGH',
            'mcp_name': mcp_name,
            'details': f"Package {mcp_name} has been unpublished from registry"
        }
    
    # Check ownership change (if owner tracking is enabled)
    if 'previous_owner' in mcp and current_owner != mcp.get('previous_owner'):
        threat = {
            'type': 'OWNERSHIP_CHANGE',
            'severity': 'HIGH',
            'mcp_name': mcp_name,
            'previous_owner': mcp.get('previous_owner'),
            'current_owner': current_owner,
            'details': f"Ownership changed from {mcp.get('previous_owner')} to {current_owner}"
        }
    
    return threat


def write_threat_association(mcp_name: str, threat: Dict[str, Any]) -> bool:
    """Write threat association via POST /write endpoint."""
    payload = {
        'table': 'mcp_threat_associations',
        'data': {
            'mcp_name': mcp_name,
            'verdict': 'HIGH_RISK_ISOLATED',
            'threat_type': threat.get('type', 'UNKNOWN'),
            'threat_severity': threat.get('severity', 'HIGH'),
            'details': json.dumps(threat),
            'detected_at': datetime.utcnow().isoformat()
        }
    }
    
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Wrote threat association for {mcp_name}: {threat.get('type')}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to write threat association for {mcp_name}: {e}")
        return False


def log_to_audit(message: str, level: str = 'INFO') -> bool:
    """Log findings to audit_log via write_service."""
    payload = {
        'table': 'audit_log',
        'data': {
            'service': 'rug_pull_monitor',
            'level': level,
            'message': message,
            'timestamp': datetime.utcnow().isoformat()
        }
    }
    
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Audit log: [{level}] {message}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to write to audit_log: {e}")
        return False


def run_cycle() -> int:
    """
    Execute one monitoring cycle.
    
    Returns number of threats detected.
    """
    # Step 1: Send heartbeat
    send_heartbeat()
    
    # Step 2: Fetch registry metadata (last 500 by first_seen desc)
    mcps = fetch_registry_metadata(limit=500)
    logger.info(f"Fetched {len(mcps)} MCPs from registry")
    
    if not mcps:
        log_to_audit("No MCPs found in registry during cycle", "WARNING")
        return 0
    
    # Step 3: Extract MCP names and fetch stored fingerprints
    mcp_names = [mcp.get('name') for mcp in mcps if mcp.get('name')]
    fingerprints = fetch_fingerprints(mcp_names)
    
    # Step 4: Compare and detect discrepancies
    flagged_mcps = []
    
    for mcp in mcps:
        mcp_name = mcp.get('name')
        if not mcp_name:
            continue
        
        stored_fp = fingerprints.get(mcp_name)
        threat = detect_rug_pull(mcp, stored_fp)
        
        if threat:
            flagged_mcps.append((mcp_name, threat))
            logger.warning(f"Detected rug pull for {mcp_name}: {threat.get('type')}")
    
    # Step 5: Write threat associations and audit logs
    for mcp_name, threat in flagged_mcps:
        write_threat_association(mcp_name, threat)
        log_to_audit(
            f"Rug pull detected: server={mcp_name}, type={threat.get('type')}, "
            f"severity={threat.get('severity')}, verdict=HIGH_RISK_ISOLATED",
            'WARNING'
        )
    
    logger.info(f"Cycle complete: detected {len(flagged_mcps)} threats")
    return len(flagged_mcps)


def run():
    """Main daemon loop."""
    logger.info("Starting rug_pull_monitor daemon")
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Error in monitoring cycle: {e}")
        
        import time
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    def main():
        """Test mock registry fetch and assert audit log write."""
        
        # Mock registry data
        mock_registry_data = {
            'servers': [
                {
                    'name': 'pkg_a',
                    'version': '1.0.0',
                    'checksum': 'sha_abc123updated',  # Different from stored - DETECTED
                    'published': True,
                    'owner': 'user1',
                    'first_seen': '2024-01-01T00:00:00Z'
                },
                {
                    'name': 'pkg_b',
                    'version': '2.0.0',
                    'checksum': 'sha_def456',  # Matches stored - OK
                    'published': True,
                    'owner': 'user2',
                    'first_seen': '2024-01-02T00:00:00Z'
                },
                {
                    'name': 'pkg_unpublished',
                    'version': '1.5.0',
                    'checksum': 'sha_unpub789',
                    'published': False,  # Unpublished - DETECTED
                    'owner': 'user3',
                    'first_seen': '2024-01-03T00:00:00Z'
                },
                {
                    'name': 'pkg_ownership_change',
                    'version': '3.0.0',
                    'checksum': 'sha_owner999',
                    'published': True,
                    'owner': 'attacker_new',
                    'previous_owner': 'trusted_dev',  # Ownership change - DETECTED
                    'first_seen': '2024-01-04T00:00:00Z'
                }
            ]
        }
        
        # Mock fingerprint data
        mock_fingerprints = {
            'pkg_a': 'sha_abc123',  # Stored is 'sha_abc123', current is 'sha_abc123updated'
            'pkg_b': 'sha_def456',  # Matches
            'pkg_unpublished': 'sha_unpub789',  # Still exists in fingerprints
            'pkg_ownership_change': 'sha_owner999'  # But ownership changed
        }
        
        # Mock response class
        class MockResponse:
            def __init__(self, json_data, status_code=200):
                self._json_data = json_data
                self.status_code = status_code
            
            def json(self):
                return self._json_data
            
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {self.status_code}")
        
        # Track audit log writes
        audit_log_write_count = 0
        
        def mock_get(url, *args, **kwargs):
            if 'mcp_fingerprints' in url:
                return MockResponse({'fingerprints': mock_fingerprints})
            return MockResponse(mock_registry_data)
        
        def mock_post(url, json=None, *args, **kwargs):
            nonlocal audit_log_write_count
            
            # Track service_health calls
            if 'service_health' in url:
                return MockResponse({'status': 'ok'})
            
            # Track audit_log writes
            if json and json.get('table') == 'audit_log':
                audit_log_write_count += 1
            
            # Track threat_associations writes
            if json and json.get('table') == 'mcp_threat_associations':
                pass  # Just track audit logs
            
            return MockResponse({'status': 'ok'})
        
        # Apply mocks
        original_get = requests.get
        original_post = requests.post
        requests.get = mock_get
        requests.post = mock_post
        
        try:
            # Run one cycle
            threats_found = run_cycle()
            
            # Assert at least one audit log write was attempted
            assert audit_log_write_count >= 1, f"Expected at least 1 audit log write, got {audit_log_write_count}"
            
            print(f"Detected {threats_found} threats, {audit_log_write_count} audit log writes")
            print("PASS")
        finally:
            # Restore original functions
            requests.get = original_get
            requests.post = original_post
    
    main()