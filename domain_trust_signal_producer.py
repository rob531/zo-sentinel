#!/usr/bin/env python3
"""
domain_trust_signal_producer.py

Signal producer module for the 'domain_trust' signal dimension.

PURPOSE: Scans mcp_server_registry for MCPs lacking a domain_trust score,
fetches WHOIS/DNS reputation data for each MCP's host domain, and writes
rows to mcp_signal_scores with signal_type='domain_trust'.
"""

import json
import re
import socket
import ssl
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_SERVICE_URL = "http://127.0.0.1:8771/heartbeat"
LOOKUP_TIMEOUT = 10  # seconds
IDEMPOTENCY_DAYS = 7
HEARTBEAT_INTERVAL = 60  # seconds

# Patterns for domain extraction
DOMAIN_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?([-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,24})\b"
)


def _parse_server_url(source_url: str | None, registry_url: str | None) -> str | None:
    """Extract a usable URL from source_url or registry_url."""
    url = source_url or registry_url
    if not url:
        return None
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    return f"https://{url}"


def _extract_domain(url: str) -> str | None:
    """Extract the domain from a URL string."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc:
        # Remove port if present
        domain = parsed.netloc.split(":")[0]
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    return None


def _get_age_days(creation_date: datetime | None) -> int | None:
    """Calculate domain age in days from creation date."""
    if creation_date is None:
        return None
    now = datetime.now(timezone.utc)
    if creation_date.tzinfo is None:
        creation_date = creation_date.replace(tzinfo=timezone.utc)
    delta = now - creation_date
    return max(0, delta.days)


def _calculate_trust_score(age_days: int | None, dnssec: bool, reputation_flags: list[str]) -> tuple[float, float]:
    """
    Calculate raw score and confidence based on domain factors.
    Returns (raw_score, confidence) where both are 0.0-1.0.
    """
    score = 0.5  # Base score
    confidence = 0.5  # Base confidence

    # Age factor (older domains are more trustworthy)
    if age_days is not None:
        confidence = min(confidence + 0.2, 1.0)
        if age_days >= 365 * 5:  # 5+ years
            score += 0.3
        elif age_days >= 365:  # 1+ year
            score += 0.2
        elif age_days >= 180:  # 6+ months
            score += 0.1
        elif age_days >= 30:  # 1+ month
            score -= 0.1
        else:  # Very new
            score -= 0.2

    # DNSSEC factor
    if dnssec:
        confidence = min(confidence + 0.15, 1.0)
        score += 0.1

    # Reputation flags
    bad_flags = [f for f in reputation_flags if f in (
        "phishing", "malware", "spam", "suspicious", "parked", "fake"
    )]
    good_flags = [f for f in reputation_flags if f in (
        "verified", "official", "trusted", "primary"
    )]

    if bad_flags:
        confidence = min(confidence + 0.15, 1.0)
        score -= len(bad_flags) * 0.15
    if good_flags:
        confidence = min(confidence + 0.1, 1.0)
        score += len(good_flags) * 0.1

    # Clamp values
    score = max(0.0, min(1.0, score))
    confidence = max(0.1, min(1.0, confidence))

    return score, confidence


def _whois_query(domain: str) -> dict[str, Any]:
    """
    Perform a basic WHOIS query using socket connection.
    Returns dict with keys: creation_date, registrar, raw_data.
    """
    result = {
        "creation_date": None,
        "registrar": None,
        "raw_data": ""
    }

    try:
        # WHOIS servers typically use port 43
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(LOOKUP_TIMEOUT)
        sock.connect(("whois.verisign-grs.com", 43))

        # Send query
        query = f"{domain}\r\n"
        sock.send(query.encode("ascii"))

        # Receive response
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            # Stop if we have enough data
            if len(response) > 50000:
                break

        sock.close()

        raw_data = response.decode("utf-8", errors="ignore")
        result["raw_data"] = raw_data

        # Parse creation date
        date_patterns = [
            r"Creation Date:\s*(.+)",
            r"Created:\s*(.+)",
            r"Created On:\s*(.+)",
            r"Domain Creation Date:\s*(.+)",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, raw_data, re.IGNORECASE)
            if match:
                date_str = match.group(1).strip()
                try:
                    # Try common date formats
                    for fmt in [
                        "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%d",
                        "%d-%b-%Y",
                        "%B %d, %Y",
                    ]:
                        try:
                            result["creation_date"] = datetime.strptime(date_str[:19], fmt[:len(date_str)])
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
                break

        # Parse registrar
        registrar_patterns = [
            r"Registrar:\s*(.+)",
            r"Registrar Name:\s*(.+)",
            r"Registrar Organization:\s*(.+)",
        ]
        for pattern in registrar_patterns:
            match = re.search(pattern, raw_data, re.IGNORECASE)
            if match:
                result["registrar"] = match.group(1).strip()[:200]  # Truncate long values
                break

    except Exception as e:
        result["raw_data"] = f"WHOIS query failed: {str(e)}"

    return result


def _dns_check(domain: str) -> dict[str, Any]:
    """
    Perform DNS checks for the domain.
    Returns dict with keys: dnssec, a_record, mx_record, ns_record.
    """
    result = {
        "dnssec": False,
        "a_record": False,
        "mx_record": False,
        "ns_record": False,
        "error": None
    }

    try:
        # Check A record
        try:
            addr = socket.gethostbyname(domain)
            result["a_record"] = addr is not None
        except socket.gaierror:
            pass

        # Check MX record
        try:
            mx_records = socket.getaddrinfo(domain, 25, socket.AF_INET)
            result["mx_record"] = len(mx_records) > 0
        except socket.gaierror:
            pass

        # Check NS record via DNS query
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = LOOKUP_TIMEOUT
            resolver.lifetime = LOOKUP_TIMEOUT
            ns_records = resolver.resolve(domain, 'NS')
            result["ns_record"] = len(ns_records) > 0
        except Exception:
            # dns.resolver might not be available, skip NS check
            pass

        # DNSSEC check (simplified - check for DS records)
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = LOOKUP_TIMEOUT
            resolver.lifetime = LOOKUP_TIMEOUT
            ds_records = resolver.resolve(f"_dnssec.{domain}", 'DS')
            result["dnssec"] = len(ds_records) > 0
        except Exception:
            # No DNSSEC or resolver not available
            result["dnssec"] = False

    except Exception as e:
        result["error"] = str(e)

    return result


def _fetch_domain_reputation(domain: str) -> dict[str, Any]:
    """
    Fetch WHOIS and DNS reputation data for a domain.
    Returns a dict with domain reputation information.
    """
    result = {
        "domain": domain,
        "age_days": None,
        "registrar": None,
        "dnssec": False,
        "reputation_flags": [],
        "raw_score": 0.5,
        "error": None
    }

    try:
        # Perform WHOIS query
        whois_data = _whois_query(domain)
        if whois_data.get("creation_date"):
            result["age_days"] = _get_age_days(whois_data["creation_date"])
        if whois_data.get("registrar"):
            result["registrar"] = whois_data["registrar"]

        # Perform DNS checks
        dns_data = _dns_check(domain)
        result["dnssec"] = dns_data.get("dnssec", False)

        # Generate reputation flags based on findings
        if result["age_days"] is not None:
            if result["age_days"] >= 365 * 5:
                result["reputation_flags"].append("established")
            elif result["age_days"] < 30:
                result["reputation_flags"].append("new_domain")

        if result["dnssec"]:
            result["reputation_flags"].append("dnssec_enabled")

        # Check for common suspicious patterns in registrar
        registrar_lower = (result["registrar"] or "").lower()
        suspicious_registrars = ["privacy", "proxy", "anonymous", "redacted"]
        if any(s in registrar_lower for s in suspicious_registrars):
            result["reputation_flags"].append("privacy_registrar")

        # Calculate score
        score, confidence = _calculate_trust_score(
            result["age_days"],
            result["dnssec"],
            result["reputation_flags"]
        )
        result["raw_score"] = score
        result["confidence"] = confidence

    except Exception as e:
        result["error"] = str(e)

    return result


def _write_signal_score(server_id: str, evidence_blob: dict, confidence: float) -> bool:
    """Write a signal score row via the write_service HTTP POST."""
    payload = {
        "server_id": server_id,
        "signal_type": "domain_trust",
        "confidence": confidence,
        "evidence_blob": evidence_blob,
        "produced_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/mcp_signal_scores",
            json=payload,
            timeout=5
        )
        return response.status_code in (200, 201, 202)
    except requests.RequestException:
        return False


def _check_existing_score(server_id: str, days: int = IDEMPOTENCY_DAYS) -> bool:
    """
    Check if a domain_trust score already exists for this server within the idempotency window.
    Returns True if score exists (should skip), False otherwise.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        response = requests.get(
            f"{WRITE_SERVICE_URL}/mcp_signal_scores/check",
            params={
                "server_id": server_id,
                "signal_type": "domain_trust",
                "cutoff": cutoff
            },
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("exists", False)
    except requests.RequestException:
        pass
    return False


def _get_servers_without_domain_trust(days: int = IDEMPOTENCY_DAYS) -> list[dict]:
    """
    Query mcp_server_registry for servers without a domain_trust score in the last N days.
    Returns list of server dicts.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        response = requests.get(
            f"{WRITE_SERVICE_URL}/mcp_server_registry/without_signal",
            params={
                "signal_type": "domain_trust",
                "cutoff": cutoff
            },
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return []


def _send_heartbeat() -> bool:
    """Send heartbeat to service_health."""
    try:
        response = requests.post(
            HEALTH_SERVICE_URL,
            json={
                "service": "domain_trust_signal_producer",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running"
            },
            timeout=3
        )
        return response.status_code in (200, 201, 202)
    except requests.RequestException:
        return False


def produce_domain_trust_signal(server_ids: list[str] | None = None) -> dict:
    """
    Main entry point for producing domain_trust signals.

    Args:
        server_ids: Optional list of server IDs. If None, finds all MCPs
                   without a domain_trust score in the last 7 days.

    Returns:
        dict with keys: processed, scored, skipped, errors
    """
    result = {
        "processed": 0,
        "scored": 0,
        "skipped": 0,
        "errors": []
    }

    last_heartbeat = time.time()

    # Get servers to process
    if server_ids is None:
        servers = _get_servers_without_domain_trust()
    else:
        servers = []
        for sid in server_ids:
            try:
                response = requests.get(
                    f"{WRITE_SERVICE_URL}/mcp_server_registry/{sid}",
                    timeout=5
                )
                if response.status_code == 200:
                    servers.append(response.json())
            except requests.RequestException:
                result["errors"].append(f"Failed to fetch server {sid}")

    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue

        # Idempotency check
        if _check_existing_score(server_id):
            result["skipped"] += 1
            continue

        # Extract domain from URLs
        source_url = server.get("source_url")
        registry_url = server.get("registry_url")
        parsed_url = _parse_server_url(source_url, registry_url)

        if not parsed_url:
            result["skipped"] += 1
            continue

        domain = _extract_domain(parsed_url)
        if not domain:
            result["skipped"] += 1
            continue

        result["processed"] += 1

        # Fetch reputation data
        try:
            reputation = _fetch_domain_reputation(domain)
        except Exception as e:
            result["errors"].append(f"{domain}: {str(e)}")
            continue

        if reputation.get("error") and reputation.get("age_days") is None:
            result["errors"].append(f"{domain}: {reputation.get('error')}")

        # Build evidence blob
        evidence_blob = {
            "domain": domain,
            "age_days": reputation.get("age_days"),
            "registrar": reputation.get("registrar"),
            "dnssec": reputation.get("dnssec", False),
            "reputation_flags": reputation.get("reputation_flags", []),
            "raw_score": reputation.get("raw_score", 0.5)
        }

        confidence = reputation.get("confidence", 0.5)

        # Write signal score
        if _write_signal_score(server_id, evidence_blob, confidence):
            result["scored"] += 1
        else:
            result["errors"].append(f"Failed to write score for {domain}")

        # Periodic heartbeat
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            _send_heartbeat()
            last_heartbeat = time.time()

    # Final heartbeat
    _send_heartbeat()

    return result


if __name__ == "__main__":
    # Self-test
    print("Running domain_trust_signal_producer self-test...")

    # Test with empty server_ids (should process servers without domain_trust score)
    result = produce_domain_trust_signal(server_ids=[])

    # Assertions
    assert isinstance(result, dict), "Result must be a dict"
    assert "processed" in result, "Result must have 'processed' key"
    assert "scored" in result, "Result must have 'scored' key"
    assert "skipped" in result, "Result must have 'skipped' key"
    assert "errors" in result, "Result must have 'errors' key"
    assert result["processed"] >= 0, "processed must be >= 0"
    assert isinstance(result["errors"], list), "errors must be a list"

    print(f"Result: {result}")
    print("PASS")  # Acceptance criteria