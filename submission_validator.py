#!/usr/bin/env python3
"""
submission_validator.py -- ZO-SENTINEL submission validation for approval_workflow.
Validates MCP server submission requests before they enter the approval workflow.
"""

import re
import requests
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional

from url_analyser import analyse_url, is_suspicious, domain_trust_score
from known_threats import (
    KNOWN_MALICIOUS_PACKAGES,
    KNOWN_MALICIOUS_DOMAINS,
    HIGH_RISK_PATTERNS,
)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8773/execute"


def ws_query(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute SQL query against DuckDB via inference_router."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> Dict[str, Any]:
    """Write rows to DuckDB table via write_service."""
    url = f"{WRITE_SERVICE_URL}/write"
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def is_internal_ip(hostname: str) -> bool:
    """Check if hostname is localhost or internal IP address."""
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    
    ip_pattern = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )
    if ip_pattern.match(hostname):
        parts = hostname.split(".")
        first_octet = int(parts[0])
        if first_octet in (10, 127):
            return True
        if first_octet == 172:
            second_octet = int(parts[1])
            if 16 <= second_octet <= 31:
                return True
        if first_octet == 192:
            second_octet = int(parts[1])
            if second_octet == 168:
                return True
    
    if hostname.startswith("169.254."):
        return True
    
    return False


def check_url_reachability(url: str, timeout: int = 5) -> tuple[bool, Optional[str]]:
    """Check if URL is reachable via HEAD request."""
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return True, None
        return False, f"HTTP {response.status_code}"
    except requests.exceptions.Timeout:
        return False, "Request timeout"
    except requests.exceptions.ConnectionError:
        return False, "Connection failed"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def check_known_malicious(name: str, url: str) -> tuple[bool, Optional[str]]:
    """Check if name or URL matches known malicious patterns."""
    name_lower = name.lower()
    url_lower = url.lower()
    
    for malicious_pkg in KNOWN_MALICIOUS_PACKAGES:
        if malicious_pkg.lower() in name_lower:
            return True, f"Package name matches known malicious package: {malicious_pkg}"
    
    for malicious_domain in KNOWN_MALICIOUS_DOMAINS:
        if malicious_domain.lower() in url_lower:
            return True, f"URL domain matches known malicious domain: {malicious_domain}"
    
    return False, None


def check_description_injection(description: str) -> tuple[bool, List[str]]:
    """Check if description contains injection patterns."""
    warnings = []
    found = False
    
    description_lower = description.lower()
    
    for pattern in HIGH_RISK_PATTERNS:
        if re.search(pattern, description, re.IGNORECASE):
            warnings.append(f"Description contains suspicious pattern: {pattern}")
            found = True
    
    if found:
        return True, warnings
    return False, warnings


def check_duplicate_submission(name: str, url: str) -> tuple[bool, Optional[str]]:
    """Check for duplicate submission in mcp_submissions table."""
    try:
        sql = """
        SELECT id, name, url, status FROM mcp_submissions 
        WHERE LOWER(name) = LOWER(?) OR LOWER(url) = LOWER(?)
        LIMIT 1
        """
        result = ws_query(sql, [name, url])
        
        if result.get("data") and len(result["data"]) > 0:
            row = result["data"][0]
            return True, f"Duplicate found (id={row[0]}, status={row[3]})"
        
        sql_registry = """
        SELECT id, name, url, status FROM mcp_servers 
        WHERE LOWER(name) = LOWER(?) OR LOWER(url) = LOWER(?)
        LIMIT 1
        """
        result_registry = ws_query(sql_registry, [name, url])
        
        if result_registry.get("data") and len(result_registry["data"]) > 0:
            row = result_registry["data"][0]
            return True, f"Server already exists in registry (id={row[0]}, status={row[3]})"
        
        return False, None
        
    except Exception as e:
        return False, None


def validate_submission(
    name: str,
    url: str,
    description: str,
    requested_by: str
) -> Dict[str, Any]:
    """
    Validate an MCP server submission request.
    
    Args:
        name: Server name
        url: Server URL
        description: Server description
        requested_by: User requesting the submission
    
    Returns:
        Dictionary with validation results:
        {
            valid: bool,
            errors: list[str],
            warnings: list[str],
            pre_checks: dict
        }
    """
    errors: List[str] = []
    warnings: List[str] = []
    pre_checks: Dict[str, Any] = {}
    
    if not name or not name.strip():
        errors.append("Server name is required")
    
    if not description or not description.strip():
        errors.append("Description is required")
    elif len(description.strip()) < 20:
        warnings.append("Description is very short; limited analysis possible")
    
    parsed_url = None
    if url:
        try:
            parsed_url = urlparse(url)
            if not parsed_url.scheme:
                errors.append("URL must include a valid scheme (http:// or https://)")
            elif parsed_url.scheme not in ("http", "https"):
                errors.append("URL scheme must be http or https")
            
            if not parsed_url.netloc:
                errors.append("URL must include a valid host")
            elif is_internal_ip(parsed_url.netloc):
                errors.append("URL cannot point to localhost or internal IP addresses")
                pre_checks["url_analysis"] = {
                    "error": "Internal IP or localhost not allowed"
                }
        except Exception as e:
            errors.append(f"Invalid URL format: {e}")
    else:
        errors.append("URL is required")
    
    if not errors:
        url_analysis = analyse_url(url)
        pre_checks["url_analysis"] = {
            "domain": url_analysis.domain,
            "tld": url_analysis.tld,
            "is_ip_address": url_analysis.is_ip_address,
            "is_localhost": url_analysis.is_localhost,
            "is_suspicious_tld": url_analysis.is_suspicious_tld,
            "domain_length": url_analysis.domain_length,
            "domain_trust_score": domain_trust_score(url),
        }
        
        if is_suspicious(url):
            errors.append("URL appears suspicious based on domain analysis")
        
        if url_analysis.is_suspicious_tld:
            warnings.append(f"URL uses suspicious TLD: {url_analysis.tld}")
        
        if url_analysis.is_ip_address:
            warnings.append("URL uses IP address instead of domain name")
    
    if not errors:
        known_malicious, malicious_msg = check_known_malicious(name, url)
        pre_checks["known_threat_check"] = {
            "matched": known_malicious,
            "details": malicious_msg
        }
        
        if known_malicious:
            errors.append(malicious_msg)
        
        reachable, reachability_error = check_url_reachability(url, timeout=5)
        pre_checks["url_reachability"] = {
            "reachable": reachable,
            "error": reachability_error
        }
        
        if not reachable:
            if reachability_error == "Request timeout":
                warnings.append("URL is not reachable within 5s timeout (may be temporarily unavailable)")
            else:
                warnings.append(f"URL reachability check failed: {reachability_error}")
        
        duplicate, duplicate_msg = check_duplicate_submission(name, url)
        pre_checks["duplicate_check"] = {
            "is_duplicate": duplicate,
            "details": duplicate_msg
        }
        
        if duplicate:
            errors.append(f"Submission already exists: {duplicate_msg}")
        
        if description:
            has_injection, injection_warnings = check_description_injection(description)
            pre_checks["description_check"] = {
                "has_suspicious_patterns": has_injection,
                "warnings": injection_warnings
            }
            warnings.extend(injection_warnings)
    
    pre_checks["metadata"] = {
        "name_provided": bool(name and name.strip()),
        "url_provided": bool(url),
        "description_length": len(description) if description else 0,
        "requested_by": requested_by,
    }
    
    valid = len(errors) == 0
    
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "pre_checks": pre_checks
    }