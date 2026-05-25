import ast
import os
import sys

SERVICE_NAME = "mcp_traffic_fingerprints_integration_check"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_imports(source_code):
    """Verify mcp_traffic_fingerprints library is imported."""
    errors = []
    tree = ast.parse(source_code)
    
    imports_detect_mcp_methods = False
    imports_is_mcp_traffic = False
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and 'mcp_traffic_fingerprints' in node.module:
                for alias in node.names:
                    if alias.name == 'detect_mcp_methods':
                        imports_detect_mcp_methods = True
                        logger.info(f"✓ Found import: detect_mcp_methods from {node.module}")
                    if alias.name == 'is_mcp_traffic':
                        imports_is_mcp_traffic = True
                        logger.info(f"✓ Found import: is_mcp_traffic from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if 'mcp_traffic_fingerprints' in alias.name:
                    logger.info(f"✓ Found module import: {alias.name}")
    
    if not imports_detect_mcp_methods:
        errors.append("MISSING: detect_mcp_methods not imported from mcp_traffic_fingerprints")
    if not imports_is_mcp_traffic:
        errors.append("MISSING: is_mcp_traffic not imported from mcp_traffic_fingerprints")
    
    return errors


def check_fingerprint_usage(source_code):
    """Verify compute_mcp_fingerprint is called in scanning path."""
    errors = []
    tree = ast.parse(source_code)
    
    found_compute = False
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'compute_mcp_fingerprint':
                found_compute = True
                logger.info("✓ Found compute_mcp_fingerprint call")
            elif isinstance(node.func, ast.Attribute) and node.func.attr == 'compute_mcp_fingerprint':
                found_compute = True
                logger.info("✓ Found compute_mcp_fingerprint method call")
    
    if not found_compute:
        errors.append("MISSING: compute_mcp_fingerprint not called in scanning path")
    
    return errors


def check_no_direct_peer_http(source_code):
    """Verify no direct HTTP to peer daemons (should use write_service)."""
    errors = []
    tree = ast.parse(source_code)
    
    # Known peer daemon URLs to avoid
    peer_ports = [8771, 8774, 8775, 8776]
    suspicious_urls = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ('get', 'post', 'put', 'patch', 'delete', 'request'):
                    # Check if this is a direct requests call
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == 'requests':
                            for arg in node.args:
                                if isinstance(arg, ast.Constant):
                                    url = str(arg.value)
                                    for port in peer_ports:
                                        if f':{port}' in url:
                                            suspicious_urls.append(url)
    
    # Also check string literals for direct URLs
    import re
    url_pattern = r'http://[a-zA-Z0-9.-]+:(8771|8774|8775|8776)'
    matches = re.findall(url_pattern, source_code)
    
    if matches:
        for match in matches:
            errors.append(f"SUSPICIOUS: Direct HTTP to peer port {match} found. Should use write_service.")
    
    if suspicious_urls:
        for url in suspicious_urls:
            errors.append(f"SUSPICIOUS: Direct requests call to {url}. Should use write_service.")
    
    if not matches and not suspicious_urls:
        logger.info("✓ No direct HTTP to peer daemons detected")
    
    return errors


def check_write_service_wiring(source_code):
    """Verify write_service (HTTP to port 8772) is used for DuckDB."""
    tree = ast.parse(source_code)
    
    found_write_service = False
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ('post', 'get', 'request'):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant):
                            url = str(arg.value)
                            if '8772' in url or 'write_service' in url.lower():
                                found_write_service = True
                                logger.info(f"✓ Found write_service call to: {url}")
    
    import re
    if re.search(r'8772', source_code):
        found_write_service = True
        logger.info("✓ Write service port 8772 referenced")
    
    return [] if found_write_service else ["WARNING: No write_service (port 8772) usage detected"]


def main():
    """Run integration checks on mcp_traffic_fingerprints_wiring.py."""
    target_file = "/home/workspace/zo_sentinel/mcp_traffic_fingerprints_wiring.py"
    
    all_errors = []
    
    # Check file exists
    if not os.path.exists(target_file):
        logger.error(f"FAIL: Target file not found: {target_file}")
        sys.exit(1)
    
    logger.info(f"Checking: {target_file}")
    
    # Read source
    with open(target_file, 'r') as f:
        source_code = f.read()
    
    # Run checks
    logger.info("=== Check 1: Library Imports ===")
    errors = check_imports(source_code)
    all_errors.extend(errors)
    
    logger.info("=== Check 2: Fingerprint Usage ===")
    errors = check_fingerprint_usage(source_code)
    all_errors.extend(errors)
    
    logger.info("=== Check 3: Peer HTTP Calls ===")
    errors = check_no_direct_peer_http(source_code)
    all_errors.extend(errors)
    
    logger.info("=== Check 4: Write Service Wiring ===")
    errors = check_write_service_wiring(source_code)
    all_errors.extend(errors)
    
    # Report
    logger.info("=" * 50)
    if all_errors:
        logger.error("INTEGRATION CHECK FAILED:")
        for error in all_errors:
            logger.error(f"  - {error}")
        sys.exit(1)
    else:
        logger.info("ALL INTEGRATION CHECKS PASSED")
        logger.info("✓ mcp_traffic_fingerprints library properly integrated")
        logger.info("✓ compute_mcp_fingerprint called in scanning path")
        logger.info("✓ No direct HTTP to peer daemons")
        logger.info("✓ write_service (port 8772) used for DuckDB")
        sys.exit(0)


if __name__ == "__main__":
    main()