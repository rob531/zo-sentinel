#!/usr/bin/env python3
"""
registry_reconciler.py -- ZO-SENTINEL Registry Reconciler Daemon.
Reconciles npm @modelcontextprotocol scope and GitHub topic:mcp-server
with the mcp_server_registry table every 43200 seconds.
"""
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Set, Any, Optional

import requests

SERVICE_NAME = 'registry_reconciler'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
QUERY_URL = 'http://127.0.0.1:8773/query'
HEARTBEAT_INTERVAL = 60
RECONCILE_INTERVAL = 43200

log = logging.getLogger(__name__)

NPM_SCOPE_URL = "https://registry.npmjs.org/-/v1/search"
NPM_ORG_URL = "https://registry.npmjs.org/@modelcontextprotocol"
GITHUB_API_URL = "https://api.github.com/search/repositories"

def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.warning(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup(signum, frame):
        if os.path.exists(pid_file):
            os.remove(pid_file)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def send_heartbeat():
    """Send service heartbeat."""
    try:
        requests.post(WRITE_SERVICE_URL, json={
            'table': 'service_health',
            'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()},
            'wait': True
        }, timeout=10)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute query via inference router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get('data', [])
    except Exception as e:
        log.error(f"Query failed: {sql[:100]} - {e}")
        return []

def ws_write(table: str, rows: Any, wait: bool = True) -> bool:
    """Write to write_service."""
    payload = {'table': table, 'rows': rows, 'wait': wait}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed to {table}: {e}")
        return False

def get_with_retry(url: str, params: Optional[Dict[str, Any]] = None, retries: int = 3) -> Optional[Dict[str, Any]]:
    """Fetch URL with retry logic."""
    headers = {'User-Agent': 'ZO-SENTINEL/1.0'}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 403:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"Fetch attempt {attempt + 1} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

def fetch_npm_packages() -> Set[str]:
    """Fetch all packages from @modelcontextprotocol scope."""
    packages = set()
    
    data = get_with_retry(NPM_ORG_URL)
    if data and 'packages' in data:
        for pkg in data['packages']:
            if pkg.get('scope') == '@modelcontextprotocol':
                name = pkg.get('name', '').replace('@modelcontextprotocol/', '')
                if name:
                    packages.add(name)
    
    offset = 0
    while True:
        data = get_with_retry(NPM_SCOPE_URL, params={
            'text': 'keywords:modelcontextprotocol',
            'size': 250,
            'from': offset
        })
        if not data or not data.get('objects'):
            break
        
        for obj in data['objects']:
            pkg = obj.get('package', {})
            name = pkg.get('name', '')
            if name.startswith('@modelcontextprotocol/'):
                short_name = name.replace('@modelcontextprotocol/', '')
                packages.add(short_name)
            elif 'modelcontextprotocol' in pkg.get('keywords', []):
                packages.add(name)
        
        if len(data['objects']) < 250:
            break
        offset += 250
        time.sleep(0.5)
    
    log.info(f"Fetched {len(packages)} npm MCP packages")
    return packages

def fetch_npm_package_details(package_names: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch details for npm packages."""
    details = {}
    for name in package_names:
        url = f"https://registry.npmjs.org/@modelcontextprotocol/{name}"
        data = get_with_retry(url)
        if data:
            latest = data.get('dist-tags', {}).get('latest', '')
            version_data = data.get('versions', {}).get(latest, {})
            details[name] = {
                'description': data.get('description', ''),
                'version': latest,
                'repository': version_data.get('repository', {}).get('url', '') if version_data else '',
                'homepage': data.get('homepage', ''),
                'license': data.get('license', ''),
            }
        time.sleep(0.2)
    return details

def fetch_github_mcp_servers() -> Set[str]:
    """Fetch MCP servers from GitHub topic search."""
    servers = set()
    page = 1
    per_page = 100
    
    while page <= 10:
        data = get_with_retry(GITHUB_API_URL, params={
            'q': 'topic:mcp-server in:readme',
            'sort': 'stars',
            'order': 'desc',
            'per_page': per_page,
            'page': page
        })
        if not data or data.get('total_count', 0) == 0:
            break
        
        for repo in data.get('items', []):
            servers.add(repo.get('full_name', ''))
        
        if len(data.get('items', [])) < per_page:
            break
        page += 1
        time.sleep(1)
    
    log.info(f"Fetched {len(servers)} GitHub MCP servers")
    return servers

def get_registry_server_ids() -> Dict[str, Dict[str, Any]]:
    """Get all server_id entries from registry."""
    sql = "SELECT server_id, name, url, scan_count, last_seen FROM mcp_server_registry"
    results = ws_query(sql)
    registry = {}
    for row in results:
        server_id = row.get('server_id', '')
        registry[server_id] = {
            'name': row.get('name', ''),
            'url': row.get('url', ''),
            'scan_count': row.get('scan_count', 0),
            'last_seen': row.get('last_seen', ''),
            'registry_source': 'unknown'
        }
        
        if row.get('url', ''):
            if 'npmjs.com' in row.get('url', '') or 'npm' in row.get('url', ''):
                registry[server_id]['registry_source'] = 'npm'
            elif 'github.com' in row.get('url', ''):
                registry[server_id]['registry_source'] = 'github'
    
    return registry

def compute_server_id(name: str, source: str = 'npm') -> str:
    """Compute consistent server_id."""
    if source == 'github':
        normalized = name.lower().replace('/', '_').replace('-', '_')
    else:
        normalized = name.lower().replace('/', '_').replace('-', '_')
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]

def reconcile_npm_packages(npm_packages: Set[str], registry: Dict[str, Dict[str, Any]]) -> tuple:
    """Reconcile npm packages with registry."""
    new_entries = []
    deprecated_entries = []
    updated_entries = []
    
    npm_by_short = {}
    for server_id, info in registry.items():
        if info['registry_source'] == 'npm':
            name = info['name'] or ''
            if name.startswith('@modelcontextprotocol/'):
                npm_by_short[name.replace('@modelcontextprotocol/', '')] = server_id
            else:
                npm_by_short[name] = server_id
    
    for pkg_name in npm_packages:
        server_id = compute_server_id(pkg_name, 'npm')
        full_name = f"@modelcontextprotocol/{pkg_name}"
        
        if server_id not in registry:
            new_entries.append({
                'server_id': server_id,
                'name': full_name,
                'registry_source': 'npm',
                'url': f"https://www.npmjs.com/package/{full_name}",
                'description': '',
                'first_seen': datetime.now(timezone.utc).isoformat(),
                'last_seen': datetime.now(timezone.utc).isoformat(),
                'scan_count': 1,
                'status': 'active'
            })
        else:
            info = registry[server_id]
            new_scan_count = (info.get('scan_count', 0) or 0) + 1
            updated_entries.append({
                'server_id': server_id,
                'scan_count': new_scan_count,
                'last_seen': datetime.now(timezone.utc).isoformat(),
                'status': 'active'
            })
    
    for server_id, info in registry.items():
        if info['registry_source'] == 'npm':
            name = info['name'] or ''
            short_name = name.replace('@modelcontextprotocol/', '') if name.startswith('@modelcontextprotocol/') else name
            
            if name and name not in npm_packages and short_name not in npm_packages:
                deprecated_entries.append({
                    'server_id': server_id,
                    'status': 'deprecated',
                    'deprecated_at': datetime.now(timezone.utc).isoformat()
                })
    
    return new_entries, deprecated_entries, updated_entries

def reconcile_github_servers(github_servers: Set[str], registry: Dict[str, Dict[str, Any]]) -> tuple:
    """Reconcile GitHub MCP servers with registry."""
    new_entries = []
    deprecated_entries = []
    updated_entries = []
    
    github_by_name = {}
    for server_id, info in registry.items():
        if info['registry_source'] == 'github':
            github_by_name[info['name']] = server_id
    
    for repo_name in github_servers:
        server_id = compute_server_id(repo_name, 'github')
        
        if server_id not in registry:
            new_entries.append({
                'server_id': server_id,
                'name': repo_name,
                'registry_source': 'github',
                'url': f"https://github.com/{repo_name}",
                'description': '',
                'first_seen': datetime.now(timezone.utc).isoformat(),
                'last_seen': datetime.now(timezone.utc).isoformat(),
                'scan_count': 1,
                'status': 'active'
            })
        else:
            info = registry[server_id]
            new_scan_count = (info.get('scan_count', 0) or 0) + 1
            updated_entries.append({
                'server_id': server_id,
                'scan_count': new_scan_count,
                'last_seen': datetime.now(timezone.utc).isoformat(),
                'status': 'active'
            })
    
    for server_id, info in registry.items():
        if info['registry_source'] == 'github':
            name = info['name'] or ''
            if name and name not in github_servers:
                deprecated_entries.append({
                    'server_id': server_id,
                    'status': 'deprecated',
                    'deprecated_at': datetime.now(timezone.utc).isoformat()
                })
    
    return new_entries, deprecated_entries, updated_entries

def apply_registry_updates(new_entries: List[Dict], deprecated_entries: List[Dict], updated_entries: List[Dict]):
    """Apply registry updates via write_service."""
    new_count = 0
    deprecated_count = 0
    updated_count = 0
    
    if new_entries:
        if ws_write('mcp_server_registry', new_entries, wait=True):
            new_count = len(new_entries)
            log.info(f"Added {new_count} new packages to registry")
    
    if deprecated_entries:
        for entry in deprecated_entries:
            sql = "UPDATE mcp_server_registry SET status = 'deprecated', last_seen = ? WHERE server_id = ?"
            result = ws_query(f"""
                INSERT INTO mcp_server_registry (server_id, status, last_seen) 
                VALUES (?, 'deprecated', ?)
                ON CONFLICT (server_id) DO UPDATE SET status = 'deprecated', last_seen = excluded.last_seen
            """.replace('?', '%s'), [entry['server_id'], entry.get('deprecated_at', datetime.now(timezone.utc).isoformat())])
            deprecated_count += 1
        log.info(f"Deprecated {deprecated_count} packages")
    
    if updated_entries:
        for entry in updated_entries:
            sql = f"""
                UPDATE mcp_server_registry 
                SET scan_count = {entry['scan_count']}, 
                    last_seen = '{entry['last_seen']}',
                    status = 'active'
                WHERE server_id = '{entry['server_id']}'
            """
            ws_query(sql)
            updated_count += 1
        log.info(f"Updated {updated_count} packages")
    
    return new_count, deprecated_count, updated_count

def log_reconciliation_complete(new_count: int, deprecated_count: int, updated_count: int, 
                                  npm_new: int, npm_deprecated: int, npm_updated: int,
                                  github_new: int, github_deprecated: int, github_updated: int):
    """Log reconciliation complete event."""
    event_data = {
        'event_type': 'reconciliation_complete',
        'service': SERVICE_NAME,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'npm': {'new': npm_new, 'deprecated': npm_deprecated, 'updated': npm_updated},
            'github': {'new': github_new, 'deprecated': github_deprecated, 'updated': github_updated},
            'totals': {'new': new_count, 'deprecated': deprecated_count, 'updated': updated_count}
        }
    }
    ws_write('mesh_events', event_data, wait=True)

def run():
    """Main reconciliation loop."""
    check_single_instance()
    log.info(f"Starting {SERVICE_NAME} - reconciliation interval: {RECONCILE_INTERVAL}s")
    
    send_heartbeat()
    
    while True:
        try:
            log.info("Starting registry reconciliation cycle")
            
            registry = get_registry_server_ids()
            log.info(f"Current registry size: {len(registry)} entries")
            
            log.info("Fetching npm packages from @modelcontextprotocol scope")
            npm_packages = fetch_npm_packages()
            
            log.info("Fetching GitHub MCP servers")
            github_servers = fetch_github_mcp_servers()
            
            npm_new, npm_deprecated, npm_updated = [], [], []
            github_new, github_deprecated, github_updated = [], [], []
            
            if npm_packages:
                npm_new, npm_deprecated, npm_updated = reconcile_npm_packages(npm_packages, registry)
            
            if github_servers:
                github_new, github_deprecated, github_updated = reconcile_github_servers(github_servers, registry)
            
            all_new = npm_new + github_new
            all_deprecated = npm_deprecated + github_deprecated
            all_updated = npm_updated + github_updated
            
            new_count, deprecated_count, updated_count = apply_registry_updates(
                all_new, all_deprecated, all_updated
            )
            
            log_reconciliation_complete(
                new_count, deprecated_count, updated_count,
                len(npm_new), len(npm_deprecated), len(npm_updated),
                len(github_new), len(github_deprecated), len(github_updated)
            )
            
            log.info(f"Reconciliation complete: {new_count} new, {updated_count} updated, {deprecated_count} deprecated")
            
        except Exception as e:
            log.error(f"Reconciliation error: {e}", exc_info=True)
        
        for _ in range(RECONCILE_INTERVAL // HEARTBEAT_INTERVAL):
            time.sleep(HEARTBEAT_INTERVAL)
            send_heartbeat()

if __name__ == "__main__":
    run()