import logging
import os
import hashlib
import hmac
import json
import time
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/github_pr_checker_webhook_wiring_complete.log')]
)
log = logging.getLogger('github_pr_checker_webhook_wiring_complete')

SERVICE_NAME = 'github_pr_checker_webhook_wiring_complete'
SERVICE_PORT = 8783
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = '/tmp/github_pr_checker_webhook_wiring_complete.pid'
GITHUB_WEBHOOK_SECRET = os.environ.get('GITHUB_WEBHOOK_SECRET', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

app = FastAPI()

_process_start_time = None

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f'ws_write failed: {e}')
        return False

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL + '/query',
            json={'sql': sql},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        return None
    except Exception as e:
        log.error(f'ws_query failed: {e}')
        return None

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL + '/execute',
            json={'sql': sql},
            timeout=30
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f'ws_execute failed: {e}')
        return False

def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f'Another instance is running with PID {old_pid}')
            return False
        except OSError:
            log.info(f'Removing stale PID file from {old_pid}')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    return True

def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass

def signal_handler(signum, frame):
    log.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)

def verify_github_signature(payload_bytes: bytes, signature: str) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        log.warning('GITHUB_WEBHOOK_SECRET not set, skipping signature verification')
        return True
    expected_sig = 'sha256=' + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature)

def get_github_headers() -> Dict[str, str]:
    headers = {'Accept': 'application/vnd.github.v3+json'}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'
    return headers

def extract_mcp_server_ids_from_pr(pr_body: str, pr_title: str) -> List[str]:
    """Extract MCP server IDs from PR body/title. Look for known patterns."""
    import re
    text = f'{pr_title}\n{pr_body or ""}'
    server_ids = []
    patterns = [
        r'server_id["\']?\s*[:=]\s*["\']?([a-f0-9\-]{36})',
        r'mcp[_-]server[_-]id["\']?\s*[:=]\s*["\']?([a-f0-9\-]{36})',
        r'\[server_id=([a-f0-9\-]{36})\]',
        r'sentinel/registry/([a-f0-9\-]{36})',
        r'mcp_server_registry.*?id.*?([a-f0-9\-]{36})',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        server_ids.extend(matches)
    return list(set(server_ids))

def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    """Get verdict for a server from registry."""
    sql = f"SELECT server_id, name, verdict, trust_score, risk_tier FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return None

def check_mcp_safety(server_id: str) -> Dict[str, Any]:
    """Check MCP server safety using registry data."""
    verdict_map = {
        'TRUSTED': 'success',
        'ENTERPRISE_CONTROLLED': 'success',
        'AMBER_UNVERIFIED': 'neutral',
        'CAUTION_LIMITED': 'neutral',
        'HIGH_RISK_ISOLATED': 'failure',
        'KNOWN_THREAT': 'failure',
        'UNKNOWN': 'neutral'
    }
    server = get_server_verdict(server_id)
    if not server:
        return {
            'status': 'in_progress',
            'conclusion': 'neutral',
            'title': 'MCP server not found in registry',
            'summary': f'Server {server_id} not found in Sentinel registry'
        }
    verdict = server.get('verdict', 'UNKNOWN')
    status = verdict_map.get(verdict, 'neutral')
    title = f'MCP Safety Check: {server.get("name", "Unknown")}'
    risk_tier = server.get('risk_tier', 'N/A')
    trust_score = server.get('trust_score', 0)
    summary = f'Verdict: {verdict}, Risk Tier: {risk_tier}, Trust Score: {trust_score}'
    return {
        'status': 'completed',
        'conclusion': status,
        'title': title,
        'summary': summary,
        'server_id': server_id,
        'verdict': verdict,
        'risk_tier': risk_tier,
        'trust_score': trust_score
    }

def post_check_run结论(owner: str, repo: str, sha: str, conclusion: str, title: str, summary: str, server_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Post check run conclusion to GitHub."""
    if not GITHUB_TOKEN:
        log.warning('GITHUB_TOKEN not set, cannot post check run')
        return None
    url = f'https://api.github.com/repos/{owner}/{repo}/check-runs'
    headers = get_github_headers()
    headers['Content-Type'] = 'application/json'
    body = {
        'name': 'Sentinel MCP Safety Check',
        'head_sha': sha,
        'status': 'completed',
        'conclusion': conclusion,
        'output': {
            'title': title,
            'summary': summary
        }
    }
    if server_id:
        body['external_id'] = server_id
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code in (200, 201):
            log.info(f'Check run posted successfully: {conclusion}')
            return resp.json()
        else:
            log.error(f'Failed to post check run: {resp.status_code} {resp.text}')
            return None
    except Exception as e:
        log.error(f'Error posting check run: {e}')
        return None

def parse_pr_url(pr_url: str) -> Optional[tuple]:
    """Parse PR URL to extract owner, repo, pr number."""
    import re
    pattern = r'github\.com[/:]([^/]+)/([^/]+)/pull/(\d+)'
    match = re.search(pattern, pr_url)
    if match:
        return match.group(1), match.group(2), int(match.group(3))
    return None

def fetch_pr_files(owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
    """Fetch files changed in a PR."""
    if not GITHUB_TOKEN:
        return []
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files'
    headers = get_github_headers()
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception as e:
        log.error(f'Error fetching PR files: {e}')
        return []

def extract_mcp_references_from_files(files: List[Dict[str, Any]]) -> List[str]:
    """Extract MCP server references from PR file changes."""
    import re
    server_ids = []
    for file in files:
        filename = file.get('filename', '')
        patch = file.get('patch', '')
        content = f'{filename}\n{patch}'
        matches = re.findall(r'server_id["\']?\s*[:=]\s*["\']?([a-f0-9\-]{36})', content)
        server_ids.extend(matches)
    return list(set(server_ids))

@app.post('/webhook/github/pr')
async def handle_github_pr_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """Handle GitHub PR webhook events."""
    payload_bytes = await request.body()
    if x_hub_signature_256 and not verify_github_signature(payload_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail='Invalid signature')
    try:
        event = request.headers.get('X_GitHub_Event', '')
        delivery_id = request.headers.get('X_GitHub_Delivery', 'unknown')
        payload = json.loads(payload_bytes.decode('utf-8'))
        log.info(f'Received GitHub webhook: event={event}, delivery={delivery_id}')
        if event == 'pull_request':
            return await handle_pull_request_webhook(payload)
        elif event == 'check_run':
            return await handle_check_run_webhook(payload)
        else:
            return JSONResponse({'status': 'ignored', 'reason': f'Event {event} not handled'})
    except Exception as e:
        log.error(f'Error handling webhook: {e}')
        raise HTTPException(status_code=400, detail=str(e))

async def handle_pull_request_webhook(payload: Dict[str, Any]) -> JSONResponse:
    """Process pull_request event."""
    action = payload.get('action', '')
    pr = payload.get('pull_request', {})
    pr_url = pr.get('html_url', '')
    pr_number = pr.get('number', 0)
    pr_title = pr.get('title', '')
    pr_body = pr.get('body', '') or ''
    sha = pr.get('head', {}).get('sha', '')
    repo = payload.get('repository', {})
    owner = repo.get('owner', {}).get('login', '')
    repo_name = repo.get('name', '')
    parsed = parse_pr_url(pr_url)
    if not parsed:
        return JSONResponse({'status': 'error', 'reason': 'Could not parse PR URL'})
    owner, repo_name, pr_number = parsed
    log.info(f'Processing PR #{pr_number} in {owner}/{repo_name}: {pr_title}')
    server_ids = extract_mcp_server_ids_from_pr(pr_body, pr_title)
    files = fetch_pr_files(owner, repo_name, pr_number)
    server_ids.extend(extract_mcp_references_from_files(files))
    server_ids = list(set(server_ids))
    if not server_ids:
        log.info(f'No MCP server IDs found in PR #{pr_number}')
        ws_write('audit_log', [{
            'event_type': 'webhook_received',
            'detail': f'No MCP servers found in PR #{pr_number}',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'actor': 'github_webhook'
        }])
        return JSONResponse({'status': 'success', 'mcp_servers_found': 0})
    log.info(f'Found {len(server_ids)} MCP server IDs: {server_ids}')
    results = []
    for server_id in server_ids:
        safety = check_mcp_safety(server_id)
        conclusion = safety.get('conclusion', 'neutral')
        post_check_run结论(owner, repo_name, sha, conclusion, safety['title'], safety['summary'], server_id)
        results.append({
            'server_id': server_id,
            'verdict': safety.get('verdict'),
            'risk_tier': safety.get('risk_tier'),
            'conclusion': conclusion
        })
    ws_write('audit_log', [{
        'event_type': 'webhook_received',
        'detail': json.dumps({'pr_number': pr_number, 'server_count': len(server_ids), 'results': results}),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'actor': 'github_webhook'
    }])
    return JSONResponse({'status': 'success', 'mcp_servers_found': len(server_ids), 'results': results})

async def handle_check_run_webhook(payload: Dict[str, Any]) -> JSONResponse:
    """Process check_run event (optional: handle completed checks)."""
    action = payload.get('action', '')
    check_run = payload.get('check_run', {})
    log.info(f'Check run event: {action} - {check_run.get("name", "")}')
    return JSONResponse({'status': 'ignored', 'reason': 'check_run event processed'})

@app.get('/health')
def health():
    global _process_start_time
    uptime = int(time.time() - (_process_start_time or time.time()))
    return {'status': 'ok', 'service': SERVICE_NAME, 'uptime': uptime}

@app.get('/')
def root():
    return {'service': SERVICE_NAME, 'version': '1.0.0', 'endpoints': ['/webhook/github/pr', '/health']}

def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': now,
        'status': 'running'
    }])

def run():
    global _process_start_time
    _process_start_time = time.time()
    if not check_single_instance():
        sys.exit(1)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f'Starting {SERVICE_NAME} on port {SERVICE_PORT}')
    send_heartbeat()
    uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT)

if __name__ == '__main__':
    run()