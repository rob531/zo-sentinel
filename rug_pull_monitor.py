#!/usr/bin/env python3
"""
rug_pull_monitor.py -- ZO-SENTINEL rug pull detection daemon.
Monitors trusted MCP servers for post-approval tool definition changes.
Detects tool mutations, server unreachability patterns, and domain changes.
Polls every 21600s (6 hours) with heartbeat support.
"""
import os
import sys
import json
import time
import hashlib
import signal
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from urllib.parse import urlparse

# Service constants
SERVICE_NAME = 'rug_pull_monitor'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 21600
MAX_CONSECUTIVE_FAILURES = 3

# Domain threat intelligence
SUSPICIOUS_DOMAINS = [
    "evil-mcp.io",
    "mcp-tools.ru",
    "mcpserver.xyz",
]

HIGH_RISK_TLDS = [".ru", ".xyz", ".info", ".cc", ".su", ".tk", ".ml", ".ga"]

def ws_query(sql: str, params: list = None) -> dict:
    """Execute SELECT against DuckDB via write_service /query.
    Routes to /query (not /execute) so rows come back. /execute is
    fire-and-forget and returns {ok:true} with no rows."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    # /query returns {'rows': [...]}. Normalize to always include 'data'.
    if 'rows' in body and 'data' not in body:
        body['data'] = [[r[k] for k in r.keys()] for r in body['rows']]
    return body

def ws_write(table: str, rows: dict | list, wait: bool = True) -> dict:
    """Write rows to DuckDB table via write_service."""
    url = WRITE_SERVICE_URL  # already ends in /write
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()

def get_db_path() -> str:
    """Get the DuckDB file path."""
    return os.environ.get('ZO_SENTINEL_DB', '/var/lib/zo_sentinel/sentinel.db')

def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'healthy'
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = '/var/run/zo/rug_pull_monitor.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup():
        if os.path.exists(pid_file):
            os.remove(pid_file)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def ensure_tables():
    """Ensure required tables exist."""
    tables = [
        """
        CREATE TABLE IF NOT EXISTS mcp_definition_history (
            id               BIGINT PRIMARY KEY,
            server_id        VARCHAR NOT NULL,
            snapshot_hash    VARCHAR,
            snapshot_content TEXT,
            captured_at      TIMESTAMPTZ DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mcp_threat_associations (
            id          BIGINT PRIMARY KEY,
            server_id   VARCHAR NOT NULL,
            threat_type VARCHAR,
            evidence    TEXT,
            severity    VARCHAR,
            reported_at TIMESTAMPTZ DEFAULT now()
        )
        """
    ]
    for sql in tables:
        try:
            requests.post(EXECUTE_URL, json={'sql': sql}, timeout=10)
        except Exception as e:
            print(f"Table creation note: {e}")

class RugPullMonitor:
    """Monitor trusted MCP servers for post-approval tool changes."""
    
    TRUSTED_VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH']
    
    def __init__(self):
        self.unreachable_counts: Dict[str, int] = {}
    
    def get_trusted_servers(self) -> List[Dict]:
        """Fetch servers with TRUSTED verdicts from registry."""
        sql = """
        SELECT server_id, name, url, verdict 
        FROM mcp_server_registry 
        WHERE verdict IN ('TRUSTED_GENERAL', 'TRUSTED_RESEARCH')
        """
        result = ws_query(sql)
        rows = result.get('data', [])
        return [
            {'server_id': r[0], 'name': r[1], 'url': r[2], 'verdict': r[3]}
            for r in rows
        ]
    
    def fetch_tool_definitions(self, url: str, timeout: int = 10) -> Optional[Dict]:
        """Fetch tool definitions from MCP server."""
        endpoints = ['/tools', '/manifest', '/mcp/tools', '/.well-known/mcp/tools']
        
        for endpoint in endpoints:
            try:
                full_url = url.rstrip('/') + endpoint
                resp = requests.get(full_url, timeout=timeout, headers={
                    'User-Agent': 'ZO-SENTINEL/1.0 RugPullMonitor/1.0'
                })
                if resp.status_code == 200:
                    return resp.json()
            except requests.RequestException:
                continue
        return None
    
    def compute_hash(self, tool_defs: Optional[Dict]) -> str:
        """Compute SHA256 hash of tool definitions JSON."""
        if tool_defs is None:
            return 'NONE'
        normalized = json.dumps(tool_defs, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    def get_stored_hash(self, server_id: str) -> Optional[str]:
        """Get most recent snapshot hash from mcp_definition_history."""
        sql = """
        SELECT snapshot_hash FROM mcp_definition_history 
        WHERE server_id = ? 
        ORDER BY captured_at DESC LIMIT 1
        """
        result = ws_query(sql, [server_id])
        data = result.get('data', [])
        if data and data[0]:
            return data[0][0]
        return None
    
    def store_snapshot(self, server_id: str, snapshot_hash: str, snapshot_content: Optional[Dict] = None):
        """Store new hash snapshot in mcp_definition_history."""
        try:
            id_result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 FROM mcp_definition_history")
            new_id = id_result.get('data', [[1]])[0][0]
            
            ws_write('mcp_definition_history', {
                'id': new_id,
                'server_id': server_id,
                'snapshot_hash': snapshot_hash,
                'snapshot_content': json.dumps(snapshot_content) if snapshot_content else None,
                'captured_at': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            print(f"Failed to store snapshot for {server_id}: {e}")
    
    def report_threat(self, server_id: str, threat_type: str, evidence: str, severity: str):
        """Write threat association to mcp_threat_associations."""
        try:
            id_result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 FROM mcp_threat_associations")
            new_id = id_result.get('data', [[1]])[0][0]
            
            ws_write('mcp_threat_associations', {
                'id': new_id,
                'server_id': server_id,
                'threat_type': threat_type,
                'evidence': evidence,
                'severity': severity,
                'reported_at': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            print(f"Failed to report threat for {server_id}: {e}")
    
    def check_hash_change(self, server_id: str, url: str) -> bool:
        """Check for tool definition hash change."""
        tool_defs = self.fetch_tool_definitions(url)
        current_hash = self.compute_hash(tool_defs)
        stored_hash = self.get_stored_hash(server_id)
        
        if stored_hash is None:
            self.store_snapshot(server_id, current_hash, tool_defs)
            print(f"  [*] Stored initial hash for {server_id}: {current_hash[:16]}...")
            return False
        
        if current_hash != stored_hash:
            evidence = f"hash_changed: {stored_hash[:16]}->{current_hash[:16]}"
            self.report_threat(server_id, 'tool_mutation', evidence, 'HIGH')
            self.store_snapshot(server_id, current_hash, tool_defs)
            print(f"  [!] TOOL MUTATION DETECTED: {server_id}")
            return True
        
        print(f"  [+] Tool definitions unchanged")
        return False
    
    def record_unreachable(self, server_id: str):
        """Track consecutive unreachable attempts."""
        count = self.unreachable_counts.get(server_id, 0) + 1
        self.unreachable_counts[server_id] = count
        
        if count >= MAX_CONSECUTIVE_FAILURES:
            self.report_threat(
                server_id,
                'server_unreachable',
                f'server unreachable for {count} consecutive checks',
                'MEDIUM'
            )
            self.unreachable_counts[server_id] = 0
            print(f"  [!] SERVER UNREACHABLE: {server_id} ({count} attempts)")
        else:
            print(f"  [!] Server unreachable (attempt {count}/{MAX_CONSECUTIVE_FAILURES})")
    
    def check_domain(self, url: str) -> Optional[Dict]:
        """Check URL domain for suspicious patterns."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            for sus_domain in SUSPICIOUS_DOMAINS:
                if sus_domain in domain:
                    return {
                        'threat_type': 'suspicious_domain',
                        'evidence': f'URL contains suspicious domain: {domain}',
                        'severity': 'MEDIUM'
                    }
            
            for tld in HIGH_RISK_TLDS:
                if domain.endswith(tld):
                    return {
                        'threat_type': 'high_risk_tld',
                        'evidence': f'Domain uses high-risk TLD: {domain}',
                        'severity': 'LOW'
                    }
            
            return None
        except Exception as e:
            print(f"Domain check error: {e}")
            return None
    
    def cycle(self):
        """Main monitoring cycle."""
        cycle_start = datetime.now(timezone.utc).isoformat()
        print(f"[{cycle_start}] Starting rug pull monitoring cycle")
        
        trusted_servers = self.get_trusted_servers()
        print(f"Found {len(trusted_servers)} trusted servers to monitor")
        
        mutations_detected = 0
        domains_checked = 0
        
        for server in trusted_servers:
            server_id = server['server_id']
            url = server['url']
            verdict = server['verdict']
            name = server.get('name', server_id)
            
            print(f"\nChecking server: {name} ({server_id}) - {verdict}")
            
            # Check for tool mutation
            try:
                changed = self.check_hash_change(server_id, url)
                if changed:
                    mutations_detected += 1
                # Reset unreachable counter on success
                if server_id in self.unreachable_counts:
                    self.unreachable_counts[server_id] = 0
            except requests.RequestException as e:
                print(f"  [x] Request error: {e}")
                self.record_unreachable(server_id)
            except Exception as e:
                print(f"  [x] Error checking tool defs: {e}")
                self.record_unreachable(server_id)
            
            # Re-check domain reputation
            try:
                domain_threat = self.check_domain(url)
                if domain_threat:
                    self.report_threat(
                        server_id,
                        domain_threat['threat_type'],
                        domain_threat['evidence'],
                        domain_threat['severity']
                    )
                    print(f"  [!] Domain threat detected: {domain_threat['threat_type']}")
                domains_checked += 1
            except Exception as e:
                print(f"  [x] Error checking domain: {e}")
        
        cycle_end = datetime.now(timezone.utc).isoformat()
        print(f"\n[{cycle_end}] Cycle complete: {mutations_detected} mutations, {domains_checked} domains checked")
    
    def run(self):
        """Main daemon entry point. Loops cycle -> sleep forever."""
        check_single_instance()
        ensure_tables()
        send_heartbeat()
        print(f'[{datetime.now(timezone.utc).isoformat()}] rug_pull_monitor started')
        while True:
            try:
                self.cycle()
            except Exception as e:
                print(f'Cycle error: {e}')
            send_heartbeat()
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    monitor = RugPullMonitor()
    monitor.run()
