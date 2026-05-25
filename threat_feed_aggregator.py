#!/usr/bin/env python3
"""
threat_feed_aggregator.py -- ZO-SENTINEL threat feed aggregator daemon.
Polls multiple free threat intel feeds and cross-references against MCP server registry.
"""
import os
import sys
import time
import json
import logging
import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests

# Add project path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger(__name__)

# Service configuration
SERVICE_NAME = "threat_feed_aggregator"
PORT = 8785
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
EXECUTE_URL = os.environ.get("EXECUTE_URL", "http://127.0.0.1:8772/execute")
QUERY_URL = os.environ.get("QUERY_URL", "http://127.0.0.1:8772/query")
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "300"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3600"))
PID_FILE = f"/tmp/zo_sentinel_{SERVICE_NAME}.pid"

# Feed endpoints
FEED_ENDPOINTS = {
    "cisa_kev": {
        "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "type": "json",
        "method": "GET",
    },
    "urlhaus": {
        "url": "https://urlhaus-api.abuse.ch/v1/urls/recent/limit/100/",
        "type": "json",
        "method": "GET",
    },
    "openphish": {
        "url": "https://openphish.com/feed.txt",
        "type": "text",
        "method": "GET",
    },
    "malwarebazaar": {
        "url": "https://mb-api.abuse.ch/api/v1/",
        "type": "json",
        "method": "POST",
        "data": {"query": "get_recent", "selector": "100"},
    },
}

# Headers for requests
REQUEST_HEADERS = {
    "User-Agent": "ZO-SENTINEL-ThreatAggregator/1.0 (Security Research)",
    "Accept": "application/json, text/plain, */*",
}

# Timeout for HTTP requests
REQUEST_TIMEOUT = 30


def check_single_instance() -> bool:
    """Ensure only one instance runs. Returns True if this is the sole instance."""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.warning(f"Another instance already running with PID {old_pid}")
                return False
            except OSError:
                log.info(f"Stale PID file found, removing")
                os.remove(PID_FILE)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Error checking single instance: {e}")
        return True


def remove_pid_file():
    """Remove the PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_query_url() -> str:
    return QUERY_URL


def send_heartbeat():
    """Send service heartbeat to write service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        }
        response = requests.post(get_write_url(), json=payload, timeout=10)
        if response.status_code not in (200, 201):
            log.warning(f"Heartbeat failed: {response.status_code}")
    except Exception as e:
        log.warning(f"Heartbeat error: {e}")


def ws_query(query: str) -> List[Dict[str, Any]]:
    """Execute a query against the query service."""
    try:
        response = requests.post(
            get_query_url(),
            json={"query": query},
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("data", [])
        log.warning(f"Query failed: {response.status_code} - {response.text[:200]}")
        return []
    except Exception as e:
        log.error(f"Query error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write data to a table via write service."""
    try:
        payload = {"table": table, "rows": rows}
        response = requests.post(get_write_url(), json=payload, timeout=30)
        if response.status_code in (200, 201):
            return True
        log.warning(f"Write failed for {table}: {response.status_code} - {response.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Write error for {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute SQL via execute service."""
    try:
        response = requests.post(
            get_execute_url(),
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if response.status_code in (200, 201):
            return True
        log.warning(f"Execute failed: {response.status_code} - {response.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Execute error: {e}")
        return False


def fetch_cisa_kev() -> List[Dict[str, Any]]:
    """Fetch CISA Known Exploited Vulnerabilities."""
    try:
        feed = FEED_ENDPOINTS["cisa_kev"]
        response = requests.get(feed["url"], headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            vulnerabilities = data.get("vulnerabilities", [])
            results = []
            for vuln in vulnerabilities:
                results.append({
                    "cve_id": vuln.get("cveID", ""),
                    "vendor_project": vuln.get("vendorProject", ""),
                    "product": vuln.get("product", ""),
                    "date_added": vuln.get("dateAdded", ""),
                    "short_description": vuln.get("shortDescription", ""),
                    "required_action": vuln.get("requiredAction", ""),
                    "due_date": vuln.get("dueDate", ""),
                    "known_ransomware_campaign_uses": vuln.get("knownRansomwareCampaignUses", ""),
                })
            log.info(f"Fetched {len(results)} CISA KEV entries")
            return results
        log.warning(f"CISA KEV fetch failed: {response.status_code}")
        return []
    except Exception as e:
        log.error(f"CISA KEV fetch error: {e}")
        return []


def fetch_urlhaus() -> List[Dict[str, Any]]:
    """Fetch recent malicious URLs from URLhaus."""
    try:
        feed = FEED_ENDPOINTS["urlhaus"]
        response = requests.get(feed["url"], headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if data.get("query_status") == "success":
                payloads = data.get("urls", [])
                results = []
                for entry in payloads:
                    results.append({
                        "url": entry.get("url", ""),
                        "domain": entry.get("domain", ""),
                        "ip_address": entry.get("ip_address", ""),
                        "ip_version": entry.get("ip_version", ""),
                        "country": entry.get("country", ""),
                        "status": entry.get("url_status", ""),
                        "threat_type": entry.get("tags", []),
                        "date_added": entry.get("date_added", ""),
                    })
                log.info(f"Fetched {len(results)} URLhaus entries")
                return results
        log.warning(f"URLhaus fetch failed: {response.status_code}")
        return []
    except Exception as e:
        log.error(f"URLhaus fetch error: {e}")
        return []


def fetch_openphish() -> List[Dict[str, Any]]:
    """Fetch phishing URLs from OpenPhish."""
    try:
        feed = FEED_ENDPOINTS["openphish"]
        response = requests.get(feed["url"], headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            results = []
            for line in lines:
                line = line.strip()
                if line and line.startswith("http"):
                    try:
                        parsed = urlparse(line)
                        results.append({
                            "url": line,
                            "domain": parsed.netloc,
                            "phishing_url": line,
                        })
                    except Exception:
                        continue
            log.info(f"Fetched {len(results)} OpenPhish entries")
            return results
        log.warning(f"OpenPhish fetch failed: {response.status_code}")
        return []
    except Exception as e:
        log.error(f"OpenPhish fetch error: {e}")
        return []


def fetch_malwarebazaar() -> List[Dict[str, Any]]:
    """Fetch recent malware from MalwareBazaar."""
    try:
        feed = FEED_ENDPOINTS["malwarebazaar"]
        response = requests.post(
            feed["url"],
            data=feed["data"],
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("query_status") == "ok":
                entries = data.get("data", [])
                results = []
                for entry in entries:
                    results.append({
                        "sha256_hash": entry.get("sha256_hash", ""),
                        "filename": entry.get("filename", ""),
                        "signature": entry.get("signature", ""),
                        "tags": entry.get("tags", []),
                        "url": entry.get("url", ""),
                        "first_seen": entry.get("first_seen", ""),
                        "last_seen": entry.get("last_seen", ""),
                    })
                log.info(f"Fetched {len(results)} MalwareBazaar entries")
                return results
        log.warning(f"MalwareBazaar fetch failed: {response.status_code}")
        return []
    except Exception as e:
        log.error(f"MalwareBazaar fetch error: {e}")
        return []


def get_registry_urls_and_ips() -> Tuple[Set[str], Set[str], Dict[str, str]]:
    """Get all URLs and IPs from the MCP server registry for cross-referencing."""
    try:
        query = """
        SELECT server_id, url, description 
        FROM mcp_server_registry 
        WHERE url IS NOT NULL AND url != ''
        """
        results = ws_query(query)
        
        urls = set()
        ips = set()
        server_map = {}
        
        for row in results:
            server_id = row.get("server_id", "")
            url = row.get("url", "")
            if url:
                urls.add(url.lower())
                server_map[url.lower()] = server_id
                try:
                    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
                    if parsed.netloc:
                        urls.add(parsed.netloc.lower())
                        if parsed.netloc.lower() not in server_map:
                            server_map[parsed.netloc.lower()] = server_id
                except Exception:
                    pass
        
        ip_pattern = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
        for url in list(urls):
            found_ips = ip_pattern.findall(url)
            for ip in found_ips:
                ips.add(ip)
                server_map[ip] = server_map.get(url, "")
        
        log.info(f"Loaded {len(urls)} URLs and {len(ips)} IPs from registry")
        return urls, ips, server_map
    except Exception as e:
        log.error(f"Error loading registry data: {e}")
        return set(), set(), {}


def extract_domain(url_or_host: str) -> str:
    """Extract domain from URL or hostname."""
    try:
        if not url_or_host.startswith("http"):
            url_or_host = f"https://{url_or_host}"
        parsed = urlparse(url_or_host)
        return parsed.netloc.lower()
    except Exception:
        return url_or_host.lower()


def check_domain_match(feed_domain: str, registry_domains: Set[str]) -> Optional[str]:
    """Check if feed domain matches any registry domain (substring or exact)."""
    feed_domain_lower = feed_domain.lower()
    for reg_domain in registry_domains:
        if feed_domain_lower in reg_domain or reg_domain in feed_domain_lower:
            return reg_domain
    return None


def check_ip_match(feed_ip: str, registry_ips: Set[str]) -> Optional[str]:
    """Check if feed IP matches registry IP (exact match)."""
    feed_ip = feed_ip.strip()
    if feed_ip in registry_ips:
        return feed_ip
    return None


def get_cve_packages_from_registry() -> Set[str]:
    """Get all package names and dependencies from registry for CVE matching."""
    try:
        query = """
        SELECT server_id, name, description 
        FROM mcp_server_registry 
        WHERE description IS NOT NULL
        """
        results = ws_query(query)
        
        packages = set()
        for row in results:
            name = row.get("name", "")
            if name:
                packages.add(name.lower())
                if "/" in name:
                    parts = name.split("/")
                    packages.add(parts[-1].lower())
        
        log.info(f"Loaded {len(packages)} packages from registry for CVE matching")
        return packages
    except Exception as e:
        log.error(f"Error loading packages: {e}")
        return set()


def process_cisa_kev(vulnerabilities: List[Dict], registry_packages: Set[str], 
                     server_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Process CISA KEV entries and find matches."""
    matches = []
    
    for vuln in vulnerabilities:
        cve_id = vuln.get("cve_id", "")
        vendor = vuln.get("vendor_project", "").lower()
        product = vuln.get("product", "").lower()
        description = vuln.get("short_description", "")
        
        if not cve_id:
            continue
        
        for pkg in registry_packages:
            pkg_parts = pkg.split("/")
            pkg_name = pkg_parts[-1] if pkg_parts else pkg
            
            if (pkg_name in product or pkg_name in vendor or 
                vendor in pkg or product in pkg):
                
                matches.append({
                    "server_id": server_map.get(pkg, ""),
                    "threat_type": "threat_feed_match",
                    "evidence": f"CISA KEV: {cve_id} - {vendor}/{product} - {description[:200]}",
                    "severity": "CRITICAL",
                    "indicator": cve_id,
                    "feed_source": "cisa_kev",
                    "matched_field": "cve_package",
                })
                break
    
    return matches


def process_urlhaus(urls: List[Dict], registry_urls: Set[str], registry_ips: Set[str],
                     server_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Process URLhaus entries and find matches."""
    matches = []
    
    for entry in urls:
        url = entry.get("url", "")
        domain = entry.get("domain", "")
        ip = entry.get("ip_address", "")
        
        if not url:
            continue
        
        match_key = None
        
        if url.lower() in registry_urls:
            match_key = url.lower()
        elif domain and check_domain_match(domain, registry_urls):
            match_key = check_domain_match(domain, registry_urls)
        elif ip and check_ip_match(ip, registry_ips):
            match_key = check_ip_match(ip, registry_ips)
        
        if match_key:
            server_id = server_map.get(match_key, "")
            threat_types = entry.get("threat_type", [])
            threat_str = ", ".join(threat_types) if threat_types else "malware"
            
            matches.append({
                "server_id": server_id,
                "threat_type": "threat_feed_match",
                "evidence": f"URLhaus: {url} - Threat: {threat_str} - Country: {entry.get('country', 'N/A')}",
                "severity": "CRITICAL",
                "indicator": url,
                "feed_source": "urlhaus",
                "matched_field": "url",
            })
    
    return matches


def process_openphish(urls: List[Dict], registry_urls: Set[str], 
                       server_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Process OpenPhish entries and find matches."""
    matches = []
    
    for entry in urls:
        url = entry.get("url", "")
        domain = entry.get("domain", "")
        
        if not url:
            continue
        
        match_key = None
        
        if url.lower() in registry_urls:
            match_key = url.lower()
        elif domain and check_domain_match(domain, registry_urls):
            match_key = check_domain_match(domain, registry_urls)
        
        if match_key:
            server_id = server_map.get(match_key, "")
            
            matches.append({
                "server_id": server_id,
                "threat_type": "threat_feed_match",
                "evidence": f"OpenPhish: {url} - Phishing site detected",
                "severity": "CRITICAL",
                "indicator": url,
                "feed_source": "openphish",
                "matched_field": "url",
            })
    
    return matches


def process_malwarebazaar(entries: List[Dict], server_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Process MalwareBazaar entries and find matches."""
    matches = []
    
    for entry in entries:
        sha256 = entry.get("sha256_hash", "")
        filename = entry.get("filename", "")
        signature = entry.get("signature", "")
        url = entry.get("url", "")
        
        if not sha256:
            continue
        
        for pkg, server_id in server_map.items():
            if pkg and (pkg.lower() in filename.lower() or pkg.lower() in signature.lower()):
                matches.append({
                    "server_id": server_id,
                    "threat_type": "threat_feed_match",
                    "evidence": f"MalwareBazaar: {filename} (SHA256: {sha256[:32]}...) - Signature: {signature}",
                    "severity": "CRITICAL",
                    "indicator": sha256,
                    "feed_source": "malwarebazaar",
                    "matched_field": "filename_signature",
                })
                break
    
    return matches


def record_threat_association(match: Dict[str, Any]) -> bool:
    """Record a threat association to the database."""
    try:
        rows = {
            "server_id": match.get("server_id", ""),
            "threat_type": match.get("threat_type", "threat_feed_match"),
            "evidence": match.get("evidence", ""),
            "severity": match.get("severity", "HIGH"),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        return ws_write("mcp_threat_associations", rows)
    except Exception as e:
        log.error(f"Error recording threat association: {e}")
        return False


def update_verdict_to_known_threat(server_id: str, evidence: str) -> bool:
    """Update server verdict to KNOWN_THREAT."""
    try:
        rows = {
            "server_id": server_id,
            "verdict": "KNOWN_THREAT",
            "verdict_reasoning": f"Threat feed match: {evidence[:300]}",
            "last_assessed": datetime.now(timezone.utc).isoformat(),
        }
        return ws_write("mcp_server_registry", rows)
    except Exception as e:
        log.error(f"Error updating verdict: {e}")
        return False


def store_raw_feed_data(feed_name: str, data: Any, count: int) -> bool:
    """Store raw feed data in world_articles table for historical reference."""
    try:
        rows = {
            "title": f"Threat Feed: {feed_name}",
            "content": json.dumps(data)[:10000],
            "topics": f"threat_feed,{feed_name},threat_intel",
            "source": feed_name,
            "published_date": datetime.now(timezone.utc).isoformat(),
            "url": FEED_ENDPOINTS.get(feed_name, {}).get("url", ""),
        }
        success = ws_write("world_articles", rows)
        if success:
            log.info(f"Stored {count} raw entries from {feed_name} in world_articles")
        return success
    except Exception as e:
        log.error(f"Error storing raw feed data: {e}")
        return False


def ensure_tables() -> bool:
    """Ensure required tables exist."""
    try:
        sqls = [
            """
            CREATE TABLE IF NOT EXISTS mcp_threat_associations (
                id          BIGINT PRIMARY KEY,
                server_id   VARCHAR NOT NULL,
                threat_type VARCHAR,
                evidence    TEXT,
                severity    VARCHAR,
                reported_at TIMESTAMPTZ DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS world_articles (
                id             BIGINT PRIMARY KEY,
                title          VARCHAR,
                content        TEXT,
                topics         VARCHAR,
                source         VARCHAR,
                published_date TIMESTAMPTZ,
                url            VARCHAR,
                created_at     TIMESTAMPTZ DEFAULT now()
            )
            """,
        ]
        for sql in sqls:
            if not ws_execute(sql):
                return False
        return True
    except Exception as e:
        log.error(f"Error ensuring tables: {e}")
        return False


def process_all_feeds() -> Tuple[int, int]:
    """Process all threat feeds and return (matches_found, threats_recorded)."""
    log.info("Starting threat feed processing cycle")
    
    if not ensure_tables():
        log.error("Failed to ensure required tables exist")
        return 0, 0
    
    registry_urls, registry_ips, server_map = get_registry_urls_and_ips()
    registry_packages = get_cve_packages_from_registry()
    
    if not registry_urls and not registry_ips and not registry_packages:
        log.warning("No registry data available for cross-referencing")
    
    total_matches = 0
    threats_recorded = 0
    
    try:
        log.info("Fetching CISA KEV...")
        cisa_data = fetch_cisa_kev()
        if cisa_data:
            store_raw_feed_data("cisa_kev", cisa_data, len(cisa_data))
            if registry_packages:
                matches = process_cisa_kev(cisa_data, registry_packages, server_map)
                for match in matches:
                    total_matches += 1
                    if record_threat_association(match) and match.get("server_id"):
                        threats_recorded += 1
                        update_verdict_to_known_threat(
                            match["server_id"], 
                            match.get("evidence", "")
                        )
    except Exception as e:
        log.error(f"Error processing CISA KEV: {e}")
    
    try:
        log.info("Fetching URLhaus...")
        urlhaus_data = fetch_urlhaus()
        if urlhaus_data:
            store_raw_feed_data("urlhaus", urlhaus_data, len(urlhaus_data))
            matches = process_urlhaus(urlhaus_data, registry_urls, registry_ips, server_map)
            for match in matches:
                total_matches += 1
                if record_threat_association(match) and match.get("server_id"):
                    threats_recorded += 1
                    update_verdict_to_known_threat(
                        match["server_id"],
                        match.get("evidence", "")
                    )
    except Exception as e:
        log.error(f"Error processing URLhaus: {e}")
    
    try:
        log.info("Fetching OpenPhish...")
        openphish_data = fetch_openphish()
        if openphish_data:
            store_raw_feed_data("openphish", openphish_data, len(openphish_data))
            matches = process_openphish(openphish_data, registry_urls, server_map)
            for match in matches:
                total_matches += 1
                if record_threat_association(match) and match.get("server_id"):
                    threats_recorded += 1
                    update_verdict_to_known_threat(
                        match["server_id"],
                        match.get("evidence", "")
                    )
    except Exception as e:
        log.error(f"Error processing OpenPhish: {e}")
    
    try:
        log.info("Fetching MalwareBazaar...")
        malwarebazaar_data = fetch_malwarebazaar()
        if malwarebazaar_data:
            store_raw_feed_data("malwarebazaar", malwarebazaar_data, len(malwarebazaar_data))
            matches = process_malwarebazaar(malwarebazaar_data, server_map)
            for match in matches:
                total_matches += 1
                if record_threat_association(match) and match.get("server_id"):
                    threats_recorded += 1
                    update_verdict_to_known_threat(
                        match["server_id"],
                        match.get("evidence", "")
                    )
    except Exception as e:
        log.error(f"Error processing MalwareBazaar: {e}")
    
    log.info(f"Feed processing complete: {total_matches} matches found, {threats_recorded} threats recorded")
    return total_matches, threats_recorded


def run():
    """Main run loop for the threat feed aggregator daemon."""
    if not check_single_instance():
        log.error("Another instance is already running. Exiting.")
        sys.exit(1)
    
    try:
        log.info(f"Starting {SERVICE_NAME} daemon")
        log.info(f"Write service: {WRITE_SERVICE_URL}")
        log.info(f"Query service: {QUERY_URL}")
        log.info(f"Poll interval: {POLL_INTERVAL}s")
        
        send_heartbeat()
        
        while True:
            try:
                matches, threats = process_all_feeds()
                log.info(f"Cycle complete: {matches} matches, {threats} threats recorded")
            except Exception as e:
                log.error(f"Error in processing cycle: {e}")
            
            send_heartbeat()
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        log.info("Received shutdown signal")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    run()