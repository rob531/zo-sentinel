#!/usr/bin/env python3
"""
certificate_analyser.py -- ZO-SENTINEL TLS certificate analysis daemon.
Fetches and analyses TLS certificates for HTTPS MCP servers to detect
misconfigured, expired, or suspicious certificates.
"""
import ssl
import socket
import hashlib
import base64
import struct
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import requests

# Constants
SERVICE_NAME = "certificate_analyser"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 43200

log = logging.getLogger(__name__)


@dataclass
class CertificateData:
    issuer: str
    subject: str
    valid_from: datetime
    valid_until: datetime
    san_domains: List[str]
    serial_number: str
    signature_algorithm: str
    is_self_signed: bool
    cert_hash: str


@dataclass
class CertificateRisk:
    domain_trust_delta: int
    threat_level: str
    issues: List[str]


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query via write_service query endpoint."""
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return data.get("data", [])
        log.warning(f"Query returned non-success: {data}")
        return []
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write records via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed for {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute SQL via write_service."""
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "status": "running"
                }
            },
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
        return False


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    import os
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running (PID {old_pid}), exiting.")
            return False
        except OSError:
            pass
    
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def parse_x509_subject(der_cert: bytes) -> Tuple[str, str, List[str], bool, datetime, datetime, str]:
    """Parse X.509 certificate DER data manually (no cryptography dependency)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        
        cert = x509.load_der_x509_certificate(der_cert, default_backend())
        
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        
        valid_from = cert.not_valid_before_utc
        valid_until = cert.not_valid_after_utc
        
        san_domains = []
        try:
            for ext in cert.extensions:
                if ext.oid == x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME:
                    san_ext = ext.value
                    for name in san_ext:
                        if isinstance(name, x509.DNSName):
                            san_domains.append(name.value)
        except Exception:
            pass
        
        is_self_signed = cert.issuer == cert.subject
        
        serial_number = format(cert.serial_number, 'X')
        signature_algorithm = cert.signature_algorithm_oid._name
        
        return issuer, subject, san_domains, is_self_signed, valid_from, valid_until, serial_number
        
    except ImportError:
        log.warning("cryptography library not available, using basic SSL parsing")
        return _basic_cert_parse(der_cert)


def _basic_cert_parse(der_cert: bytes) -> Tuple[str, str, List[str], bool, datetime, datetime, str]:
    """Fallback basic certificate parsing."""
    import re
    
    issuer = "Unknown"
    subject = "Unknown"
    san_domains = []
    is_self_signed = False
    valid_from = datetime.min
    valid_until = datetime.max
    serial_number = "Unknown"
    
    try:
        pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
        pem_str = pem_cert.decode('utf-8')
        
        for line in pem_str.split('\n'):
            if 'ISSUER' in line:
                match = re.search(r'CN=([^,\n]+)', line)
                if match:
                    issuer = match.group(1)
            elif 'Subject:' in line and not subject.endswith('Unknown'):
                match = re.search(r'CN=([^,\n]+)', line)
                if match:
                    subject = match.group(1)
        
        is_self_signed = (issuer == subject)
        
    except Exception as e:
        log.error(f"Basic parse error: {e}")
    
    return issuer, subject, san_domains, is_self_signed, valid_from, valid_until, serial_number


def fetch_certificate(host: str, port: int = 443, timeout: int = 10) -> Optional[bytes]:
    """Fetch TLS certificate from server."""
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                return cert_der
    except Exception as e:
        log.debug(f"Failed to fetch cert from {host}:{port}: {e}")
        return None


def compute_cert_hash(cert_der: bytes) -> str:
    """Compute SHA256 hash of certificate."""
    return hashlib.sha256(cert_der).hexdigest()


def analyse_certificate(cert_data: CertificateData, server_url: str) -> CertificateRisk:
    """Analyse certificate and compute risk scores."""
    issues = []
    domain_trust_delta = 0
    threat_level = "none"
    
    now = datetime.now(timezone.utc)
    cert_valid_until = cert_data.valid_until
    
    if cert_valid_until.tzinfo is None:
        cert_valid_until = cert_valid_until.replace(tzinfo=timezone.utc)
    
    if cert_valid_until < now:
        issues.append(f"EXPIRED: Certificate expired on {cert_valid_until.isoformat()}")
        domain_trust_delta -= 40
        threat_level = "critical"
    else:
        days_until_expiry = (cert_valid_until - now).days
        if days_until_expiry < 7:
            issues.append(f"EXPIRING_SOON: Certificate expires in {days_until_expiry} days")
            domain_trust_delta -= 20
            if threat_level != "critical":
                threat_level = "high"
        elif days_until_expiry < 30:
            issues.append(f"EXPIRING_WARNING: Certificate expires in {days_until_expiry} days")
            domain_trust_delta -= 10
    
    if cert_data.is_self_signed:
        issues.append("SELF_SIGNED: Certificate is self-signed")
        domain_trust_delta -= 30
        if threat_level not in ["critical", "high"]:
            threat_level = "medium"
    
    try:
        from urllib.parse import urlparse
        parsed_url = urlparse(server_url)
        host = parsed_url.hostname
        
        hostname_match = False
        if host:
            if cert_data.subject.startswith(f"CN={host}") or host in cert_data.subject:
                hostname_match = True
            for san in cert_data.san_domains:
                if host == san or host.endswith(f".{san}"):
                    hostname_match = True
                    break
        
        if not hostname_match and host:
            issues.append(f"HOSTNAME_MISMATCH: Certificate CN/SAN doesn't match {host}")
            domain_trust_delta -= 50
            threat_level = "critical"
            
    except Exception as e:
        log.error(f"Hostname mismatch check failed: {e}")
    
    if "Unknown" in cert_data.issuer or cert_data.issuer == cert_data.subject:
        if not cert_data.is_self_signed:
            issues.append("UNKNOWN_CA: Certificate issued by unknown/private CA")
            domain_trust_delta -= 25
            if threat_level not in ["critical"]:
                threat_level = "medium"
    
    return CertificateRisk(
        domain_trust_delta=domain_trust_delta,
        threat_level=threat_level,
        issues=issues
    )


def get_https_servers() -> List[Dict[str, Any]]:
    """Get servers with HTTPS URLs from registry."""
    sql = """
    SELECT server_id, name, url, registry_source, trust_score, verdict
    FROM mcp_server_registry
    WHERE url LIKE 'https://%'
    ORDER BY last_seen DESC
    """
    return ws_query(sql)


def ensure_tables() -> bool:
    """Ensure required tables exist."""
    create_signal_scores = """
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score REAL,
        evidence TEXT,
        scored_at TIMESTAMPTZ DEFAULT now()
    )
    """
    
    create_threat_assocs = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence TEXT,
        severity VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    
    create_cert_table = """
    CREATE TABLE IF NOT EXISTS mcp_certificate_analysis (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        cert_issuer VARCHAR,
        cert_subject VARCHAR,
        valid_from TIMESTAMPTZ,
        valid_until TIMESTAMPTZ,
        san_domains TEXT,
        is_self_signed BOOLEAN,
        cert_hash VARCHAR,
        hostname_match BOOLEAN,
        risk_level VARCHAR,
        analysed_at TIMESTAMPTZ DEFAULT now()
    )
    """
    
    return (ws_execute(create_signal_scores) and 
            ws_execute(create_threat_assocs) and 
            ws_execute(create_cert_table))


def write_signal_score(server_id: str, domain_trust_delta: int, evidence: str) -> bool:
    """Write domain trust signal score."""
    current_score = ws_query(f"""
        SELECT score FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' AND signal_name = 'domain_trust'
        ORDER BY scored_at DESC LIMIT 1
    """)
    
    base_score = 50
    if current_score:
        base_score = current_score[0].get('score', 50)
    
    new_score = max(0, min(100, base_score + domain_trust_delta))
    
    sql = f"""
    INSERT INTO mcp_signal_scores (server_id, signal_name, score, evidence)
    VALUES ('{server_id}', 'domain_trust', {new_score}, '{evidence.replace("'", "''")}')
    """
    return ws_execute(sql)


def write_threat_association(server_id: str, threat_type: str, evidence: str, severity: str) -> bool:
    """Write threat association for critical certificate issues."""
    rows = [{
        "server_id": server_id,
        "threat_type": threat_type,
        "evidence": evidence,
        "severity": severity
    }]
    return ws_write("mcp_threat_associations", rows)


def write_cert_analysis(server_id: str, cert_data: CertificateData, risk: CertificateRisk, hostname_match: bool, issues: List[str]) -> bool:
    """Write certificate analysis record."""
    valid_from = cert_data.valid_from.isoformat() if cert_data.valid_from else None
    valid_until = cert_data.valid_until.isoformat() if cert_data.valid_until else None
    san_domains = ",".join(cert_data.san_domains) if cert_data.san_domains else ""
    
    sql = f"""
    INSERT INTO mcp_certificate_analysis 
    (server_id, cert_issuer, cert_subject, valid_from, valid_until, san_domains, 
     is_self_signed, cert_hash, hostname_match, risk_level)
    VALUES (
        '{server_id}', 
        '{cert_data.issuer.replace("'", "''")}', 
        '{cert_data.subject.replace("'", "''")}',
        {f"'{valid_from}'" if valid_from else 'NULL'},
        {f"'{valid_until}'" if valid_until else 'NULL'},
        '{san_domains}',
        {cert_data.is_self_signed},
        '{cert_data.cert_hash}',
        {hostname_match},
        '{risk.threat_level}'
    )
    """
    return ws_execute(sql)


def analyse_server(server: Dict[str, Any]) -> Optional[CertificateData]:
    """Analyse certificate for a single server."""
    url = server.get('url', '')
    server_id = server.get('server_id', '')
    
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or 443
        
        if not host:
            log.warning(f"No hostname in URL for {server_id}")
            return None
        
        cert_der = fetch_certificate(host, port)
        if not cert_der:
            return None
        
        cert_hash = compute_cert_hash(cert_der)
        issuer, subject, san_domains, is_self_signed, valid_from, valid_until, serial = parse_x509_subject(cert_der)
        
        return CertificateData(
            issuer=issuer,
            subject=subject,
            valid_from=valid_from,
            valid_until=valid_until,
            san_domains=san_domains,
            serial_number=serial,
            signature_algorithm="sha256",
            is_self_signed=is_self_signed,
            cert_hash=cert_hash
        )
        
    except Exception as e:
        log.error(f"Failed to analyse certificate for {server_id}: {e}")
        return None


def run_cycle() -> int:
    """Run one analysis cycle."""
    log.info("Starting certificate analysis cycle")
    
    if not ensure_tables():
        log.error("Failed to ensure tables exist")
        return 0
    
    servers = get_https_servers()
    log.info(f"Found {len(servers)} HTTPS servers to analyse")
    
    analysed = 0
    for server in servers:
        server_id = server.get('server_id', 'unknown')
        
        try:
            cert_data = analyse_server(server)
            if not cert_data:
                continue
            
            risk = analyse_certificate(cert_data, server.get('url', ''))
            
            hostname_match = True
            issues = risk.issues
            
            if 'HOSTNAME_MISMATCH' in str(issues):
                hostname_match = False
            
            write_cert_analysis(server_id, cert_data, risk, hostname_match, issues)
            
            if risk.domain_trust_delta != 0:
                evidence = f"Certificate analysis: {'; '.join(issues)}"
                write_signal_score(server_id, risk.domain_trust_delta, evidence)
            
            if risk.threat_level == "critical":
                for issue in issues:
                    if "MISMATCH" in issue:
                        write_threat_association(
                            server_id,
                            "TLS_HOSTNAME_MISMATCH",
                            issue,
                            "CRITICAL"
                        )
                    elif "EXPIRED" in issue:
                        write_threat_association(
                            server_id,
                            "TLS_EXPIRED_CERTIFICATE",
                            issue,
                            "CRITICAL"
                        )
            
            analysed += 1
            log.info(f"Analysed {server_id}: risk={risk.threat_level}, trust_delta={risk.domain_trust_delta}")
            
        except Exception as e:
            log.error(f"Error analysing {server_id}: {e}")
            continue
    
    log.info(f"Cycle complete: {analysed} certificates analysed")
    return analysed


def heartbeat_loop():
    """Run heartbeat in background thread."""
    import threading
    def beat():
        while True:
            send_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)
    t = threading.Thread(target=beat, daemon=True)
    t.start()


def run():
    """Main daemon loop."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        return
    
    heartbeat_loop()
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Cycle failed: {e}")
        
        log.info(f"Sleeping {POLL_INTERVAL}s until next cycle")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()