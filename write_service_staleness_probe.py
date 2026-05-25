import logging
import os
import sys
import time
import requests
from datetime import datetime, timezone

SERVICE_NAME = 'write_service_staleness_probe'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_PATH = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_write(table, rows, wait=True):
    """Write to write_service via HTTP JSON RPC."""
    payload = {'table': table, 'rows': rows, 'wait': wait}
    response = requests.post(
        WRITE_SERVICE_URL + '/write',
        json=payload,
        timeout=10
    )
    response.raise_for_status()
    return response.json()


def ws_query(sql):
    """Query write_service via HTTP JSON RPC."""
    payload = {'sql': sql}
    response = requests.post(
        WRITE_SERVICE_URL + '/query',
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def check_write_service_responsive():
    """Check if write_service health endpoint responds."""
    logger.info("Checking write_service health endpoint...")
    try:
        response = requests.get(
            WRITE_SERVICE_URL + '/health',
            timeout=10
        )
        response.raise_for_status()
        health_data = response.json()
        logger.info(f"write_service health endpoint: {health_data}")
        return True, health_data
    except Exception as e:
        logger.error(f"write_service health check FAILED: {e}")
        return False, str(e)


def get_write_service_heartbeat_age():
    """Query service_health for write_service last heartbeat and compute age."""
    logger.info("Querying service_health for write_service last heartbeat...")
    
    sql = """
    SELECT 
        service_name,
        last_heartbeat,
        status,
        meta
    FROM service_health 
    WHERE service_name = 'write_service'
    ORDER BY ts DESC
    LIMIT 1
    """
    
    try:
        result = ws_query(sql)
        rows = result.get('rows', [])
        
        if not rows:
            logger.warning("No service_health entry found for write_service")
            return None, None
        
        row = rows[0]
        last_heartbeat = row.get('last_heartbeat')
        
        if last_heartbeat:
            # Compute age in seconds
            if isinstance(last_heartbeat, str):
                hb_ts = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
            else:
                hb_ts = datetime.fromisoformat(last_heartbeat)
            
            now = datetime.now(timezone.utc)
            age_seconds = (now - hb_ts).total_seconds()
            age_minutes = age_seconds / 60
            
            logger.info(f"write_service last_heartbeat: {last_heartbeat}")
            logger.info(f"Heartbeat age: {age_seconds:.1f}s ({age_minutes:.1f}m)")
            
            return age_seconds, row
        else:
            logger.warning("last_heartbeat is null in service_health")
            return None, row
            
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
        return None, None


def check_recent_restarts():
    """Check for recent write_service restart events in audit_log."""
    logger.info("Checking for recent write_service restart events...")
    
    sql = """
    SELECT 
        ts,
        target_server_id,
        action,
        details,
        actor
    FROM audit_log 
    WHERE target_server_id = 'write_service'
      AND action LIKE '%restart%'
    ORDER BY ts DESC
    LIMIT 5
    """
    
    try:
        result = ws_query(sql)
        rows = result.get('rows', [])
        
        if rows:
            logger.info(f"Found {len(rows)} recent restart event(s):")
            for row in rows:
                logger.info(f"  {row}")
            return rows
        else:
            logger.info("No recent restart events found in audit_log")
            return []
            
    except Exception as e:
        logger.error(f"Failed to query audit_log: {e}")
        return []


def check_service_registry():
    """Check write_service entry in mcp_server_registry."""
    logger.info("Checking write_service in mcp_server_registry...")
    
    sql = """
    SELECT 
        server_id,
        server_name,
        last_seen,
        last_assessed,
        status
    FROM mcp_server_registry 
    WHERE server_id = 'write_service'
    LIMIT 1
    """
    
    try:
        result = ws_query(sql)
        rows = result.get('rows', [])
        
        if rows:
            logger.info(f"mcp_server_registry entry: {rows[0]}")
            return rows[0]
        else:
            logger.info("No mcp_server_registry entry found for write_service")
            return None
            
    except Exception as e:
        logger.error(f"Failed to query mcp_server_registry: {e}")
        return None


STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes


def main():
    logger.info("=" * 60)
    logger.info("write_service staleness diagnostic probe starting")
    logger.info(f"Staleness threshold: {STALENESS_THRESHOLD_SECONDS}s ({STALENESS_THRESHOLD_SECONDS/60:.1f}m)")
    logger.info("=" * 60)
    
    diagnostics = {
        'health_endpoint_responsive': False,
        'heartbeat_age_seconds': None,
        'is_stale': None,
        'recent_restarts': [],
        'registry_entry': None
    }
    
    # 1. Check health endpoint responsiveness
    responsive, health_data = check_write_service_responsive()
    diagnostics['health_endpoint_responsive'] = responsive
    if responsive:
        logger.info("✓ write_service health endpoint is responsive")
    else:
        logger.error("✗ write_service health endpoint NOT responsive")
    
    # 2. Get heartbeat age
    age_seconds, health_row = get_write_service_heartbeat_age()
    diagnostics['heartbeat_age_seconds'] = age_seconds
    
    if age_seconds is not None:
        is_stale = age_seconds > STALENESS_THRESHOLD_SECONDS
        diagnostics['is_stale'] = is_stale
        
        if is_stale:
            logger.warning(f"⚠ write_service heartbeat is STALE: {age_seconds:.1f}s > {STALENESS_THRESHOLD_SECONDS}s threshold")
        else:
            logger.info(f"✓ write_service heartbeat is healthy: {age_seconds:.1f}s < {STALENESS_THRESHOLD_SECONDS}s threshold")
    else:
        logger.error("✗ Could not determine heartbeat age")
    
    # 3. Check for recent restarts
    diagnostics['recent_restarts'] = check_recent_restarts()
    
    # 4. Check registry
    diagnostics['registry_entry'] = check_service_registry()
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Health endpoint responsive: {diagnostics['health_endpoint_responsive']}")
    logger.info(f"Heartbeat age: {diagnostics['heartbeat_age_seconds']}s")
    logger.info(f"Is stale (>300s): {diagnostics['is_stale']}")
    logger.info(f"Recent restarts found: {len(diagnostics['recent_restarts'])}")
    logger.info(f"Registry entry exists: {diagnostics['registry_entry'] is not None}")
    
    # Determine overall status
    overall_healthy = (
        diagnostics['health_endpoint_responsive'] and
        not diagnostics['is_stale']
    )
    
    if overall_healthy:
        logger.info("")
        logger.info("✓ OVERALL: write_service appears HEALTHY")
        sys.exit(0)
    else:
        logger.warning("")
        logger.warning("⚠ OVERALL: write_service may be STALE or UNRESPONSIVE")
        logger.warning("Review diagnostics above. Do NOT rebuild write_service (protected service).")
        logger.warning("Consider: restart via supervisorctl, check disk/memory, check write_service logs")
        sys.exit(1)


if __name__ == '__main__':
    main()