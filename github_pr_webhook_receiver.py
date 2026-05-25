import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os
import hmac
import hashlib
import json
import time
import signal
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen
from urllib.error import URLError
import socketserver
import ssl

SERVICE_NAME = 'github_pr_webhook_receiver'
PORT = 8785
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
GITHUB_PR_CHECKER_PORT = None

GITHUB_PR_CHECKER_HEALTH_URL = 'http://127.0.0.1:8786/health'
KEYS_FILE = os.environ.get('GITHUB_WEBHOOK_KEYS_FILE', '/home/workspace/zo_sentinel/github_webhook_keys.json')

_processing_timeout = 5.0
_http_response_timeout = 3.0

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        if old_pid != pid and os.path.exists(f'/proc/{old_pid}'):
            print(f"[{SERVICE_NAME}] Already running with PID {old_pid}")
            sys.exit(0)
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    remove_pid_file()
    sys.exit(0)

def ws_write(table, rows):
    data = {'table': table, 'rows': rows, 'wait': True}
    req = __import__('urllib.request').Request(
        WRITE_SERVICE_URL,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def ws_query(sql):
    data = {'sql': sql}
    req = __import__('urllib.request').Request(
        QUERY_SERVICE_URL,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

def ws_execute(sql):
    data = {'sql': sql}
    req = __import__('urllib.request').Request(
        EXECUTE_SERVICE_URL,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

def send_heartbeat():
    try:
        ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': time.strftime('%Y-%m-%d %H:%M:%S')})
    except Exception as e:
        print(f"[{SERVICE_NAME}] Heartbeat failed: {e}")

def check_service_health(service_name):
    try:
        result = ws_query(f"SELECT last_heartbeat FROM service_health WHERE service = '{service_name}'")
        if result.get('rows') and len(result['rows']) > 0:
            last_heartbeat = result['rows'][0][0]
            heartbeat_time = time.mktime(time.strptime(last_heartbeat, '%Y-%m-%d %H:%M:%S'))
            if time.time() - heartbeat_time < 60:
                return True
        return False
    except Exception:
        return False

def load_webhook_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r') as f:
                keys_data = json.load(f)
                return keys_data.get('github_webhook_secrets', {})
        except Exception as e:
            print(f"[{SERVICE_NAME}] Failed to load webhook keys: {e}")
    return {}

WEBHOOK_SECRETS = load_webhook_keys()

def verify_github_signature(payload_body, signature_header, secret):
    if not signature_header or not secret:
        return False
    if not signature_header.startswith('sha256='):
        return False
    expected_signature = 'sha256=' + hmac.new(
        secret.encode('utf-8'),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

def log_to_audit(event_type, detail, actor='github_webhook', target_server_id=None):
    try:
        row = {
            'event_type': event_type,
            'actor': actor,
            'detail': json.dumps(detail),
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        if target_server_id:
            row['target_server_id'] = target_server_id
        ws_write('audit_log', row)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Audit log failed: {e}")

def extract_pr_metadata(payload):
    action = payload.get('action', 'unknown')
    pr = payload.get('pull_request', {})
    repo = payload.get('repository', {})
    sender = payload.get('sender', {})
    return {
        'action': action,
        'pr_number': pr.get('number'),
        'pr_title': pr.get('title', ''),
        'pr_state': pr.get('state', 'unknown'),
        'pr_url': pr.get('html_url', pr.get('url', '')),
        'pr_diff_url': pr.get('diff_url', ''),
        'repo_name': repo.get('full_name', ''),
        'repo_url': repo.get('html_url', ''),
        'sender_login': sender.get('login', ''),
        'sender_type': sender.get('type', ''),
        'is_draft': pr.get('draft', False),
        'head_branch': pr.get('head', {}).get('ref', ''),
        'base_branch': pr.get('base', {}).get('ref', ''),
        'additions': pr.get('additions', 0),
        'deletions': pr.get('deletions', 0),
        'changed_files': pr.get('changed_files', 0)
    }

def parse_pr_url(pr_url):
    if not pr_url:
        return None, None
    parts = pr_url.replace('https://github.com/', '').replace('http://github.com/', '').split('/')
    if len(parts) >= 5 and parts[2] == 'pull':
        return parts[1], parts[3]
    return None, None

def compute_pr_risk_tier(pr_data):
    risk_score = 0
    if pr_data.get('is_draft'):
        risk_score += 1
    if pr_data.get('changed_files', 0) > 50:
        risk_score += 3
    elif pr_data.get('changed_files', 0) > 20:
        risk_score += 2
    if pr_data.get('additions', 0) > 1000:
        risk_score += 2
    if 'dependabot' in pr_data.get('sender_login', '').lower():
        risk_score -= 2
    risk_score = max(0, min(risk_score, 10))
    if risk_score >= 7:
        return 'CRITICAL'
    elif risk_score >= 4:
        return 'HIGH'
    elif risk_score >= 2:
        return 'MEDIUM'
    return 'LOW'

def determine_pr_action(action, pr_data):
    if action in ['opened', 'reopened', 'synchronize', 'ready_for_review']:
        return 'ANALYZE'
    elif action == 'closed':
        if pr_data.get('merged'):
            return 'MERGED'
        return 'CLOSED_UNMERGED'
    elif action in ['approved', 'commented', 'review_requested']:
        return 'REVIEW'
    return 'IGNORE'

def forward_to_pr_checker(pr_metadata, event_id):
    try:
        from github_pr_checker import check_github_pr_health, fetch_and_analyze_pr
        pr_url = pr_metadata.get('pr_url', '')
        action = pr_metadata.get('action', 'unknown')
        if not pr_url:
            return {'status': 'error', 'message': 'No PR URL'}
        if action == 'closed' and not pr_metadata.get('merged'):
            return {'status': 'skipped', 'message': 'Closed unmerged PR'}
        result = fetch_and_analyze_pr(pr_url)
        if result and result.get('success'):
            return {'status': 'processed', 'result': result}
        return {'status': 'analyzed', 'result': result}
    except ImportError as e:
        print(f"[{SERVICE_NAME}] Failed to import github_pr_checker: {e}")
        return {'status': 'import_error', 'message': str(e)}
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to forward to PR checker: {e}")
        return {'status': 'error', 'message': str(e)}

def process_pr_event(payload, event_id):
    if not payload or 'pull_request' not in payload:
        return {'status': 'ignored', 'reason': 'No pull_request in payload'}
    pr_metadata = extract_pr_metadata(payload)
    action = pr_metadata.get('action', 'unknown')
    pr_action = determine_pr_action(action, pr_metadata)
    if pr_action == 'IGNORE':
        return {'status': 'ignored', 'reason': f'Action {action} not analyzed'}
    risk_tier = compute_pr_risk_tier(pr_metadata)
    log_entry = {
        'github_event_id': event_id,
        'action': action,
        'pr_action': pr_action,
        'risk_tier': risk_tier,
        'pr_number': pr_metadata.get('pr_number'),
        'repo_name': pr_metadata.get('repo_name'),
        'pr_title': pr_metadata.get('pr_title', '')[:200]
    }
    log_to_audit('github_pr_webhook_received', log_entry)
    if not check_service_health('github_pr_checker'):
        return {'status': 'skipped', 'reason': 'github_pr_checker not running'}
    result = forward_to_pr_checker(pr_metadata, event_id)
    if result.get('status') == 'processed':
        log_to_audit('github_pr_forwarded', {'event_id': event_id, 'result': result})
    return result

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

class GitHubWebhookHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def log_message(self, format, *args):
        pass
    
    def do_POST(self):
        start_time = time.time()
        event_id = self.headers.get('X-GitHub-Event', 'unknown')
        delivery_id = self.headers.get('X-GitHub-Delivery', f'{int(time.time()*1000)}')
        signature = self.headers.get('X-Hub-Signature-256', '')
        content_type = self.headers.get('Content-Type', '')
        if content_type != 'application/json':
            self.send_error_response(400, 'Content-Type must be application/json')
            return
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 10 * 1024 * 1024:
            self.send_error_response(413, 'Payload too large')
            return
        try:
            payload_body = self.rfile.read(content_length)
        except Exception as e:
            self.send_error_response(400, f'Failed to read body: {e}')
            return
        secret = WEBHOOK_SECRETS.get('default', '')
        if secret and not verify_github_signature(payload_body, signature, secret):
            print(f"[{SERVICE_NAME}] Invalid signature for event {delivery_id}")
            log_to_audit('github_webhook_signature_invalid', {'event_id': delivery_id, 'event_type': event_id})
            self.send_error_response(401, 'Invalid signature')
            return
        if event_id != 'pull_request':
            self.send_response_only(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'accepted', 'ignored': f'Event type {event_id} not handled'}).encode())
            return
        try:
            payload = json.loads(payload_body.decode('utf-8'))
        except json.JSONDecodeError as e:
            self.send_error_response(400, f'Invalid JSON: {e}')
            return
        result = None
        error_result = None
        
        def async_process():
            nonlocal result, error_result
            try:
                result = process_pr_event(payload, delivery_id)
            except Exception as e:
                error_result = str(e)
                print(f"[{SERVICE_NAME}] Processing error: {e}")
        thread = threading.Thread(target=async_process)
        thread.daemon = True
        thread.start()
        thread.join(timeout=_processing_timeout)
        if thread.is_alive():
            print(f"[{SERVICE_NAME}] Processing timeout for event {delivery_id}")
            log_to_audit('github_pr_processing_timeout', {'event_id': delivery_id})
        elif error_result:
            print(f"[{SERVICE_NAME}] Processing error: {error_result}")
        elif result:
            status = result.get('status', 'unknown')
            print(f"[{SERVICE_NAME}] Processed event {delivery_id}: {status}")
        elapsed = time.time() - start_time
        self.send_response_only(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-GitHub-Event', event_id)
        self.send_header('X-GitHub-Delivery', delivery_id)
        self.end_headers()
        response = {'status': 'accepted', 'event_id': delivery_id, 'elapsed_ms': int(elapsed * 1000)}
        try:
            self.wfile.write(json.dumps(response).encode())
        except Exception:
            pass
    
    def send_error_response(self, code, message):
        self.send_response_only(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        try:
            self.wfile.write(json.dumps({'error': message}).encode())
        except Exception:
            pass

    def do_GET(self):
        if self.path == '/health':
            self.send_response_only(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            uptime = time.time() - getattr(self.server, 'start_time', time.time())
            response = {'status': 'ok', 'service': SERVICE_NAME, 'uptime_seconds': int(uptime)}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_error_response(404, 'Not found')

def ensure_audit_table():
    try:
        ws_execute('''CREATE TABLE IF NOT EXISTS audit_log (
            id BIGINT AUTOINCREMENT PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at TIMESTAMP
        )''')
    except Exception as e:
        print(f"[{SERVICE_NAME}] Table check error (may already exist): {e}")

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_audit_table()
    server = ThreadedHTTPServer(('127.0.0.1', PORT), GitHubWebhookHandler)
    server.start_time = time.time()
    print(f"[{SERVICE_NAME}] Started on 127.0.0.1:{PORT}")
    send_heartbeat()
    last_heartbeat = time.time()
    heartbeat_interval = 30
    try:
        while True:
            server.handle_request()
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                send_heartbeat()
                last_heartbeat = now
    except KeyboardInterrupt:
        print(f"[{SERVICE_NAME}] Shutting down...")
    finally:
        remove_pid_file()
        server.server_close()

if __name__ == '__main__':
    run()