import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests

SERVICE_NAME = "runpod_session_monitor"
PORT = 0  # No HTTP server, just daemon
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
RUNPOD_API_URL = "https://api.runpod.io/graphql"
SESSION_FILE = "/home/workspace/shared/sft/runpod_session.json"
POLL_SECS = 300  # 5 minutes
HEARTBEAT_INTERVAL = 60
SSH_KEY_ENV_VAR = "zocomputerRPMay"
SSH_KEY_DEFAULT_PATH = "/run/secrets/zocomputerRPMay"
CREDITS_WARNING_THRESHOLD = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(f'/tmp/{SERVICE_NAME}.log')]
)
log = logging.getLogger(SERVICE_NAME)

_pid_file = f"/tmp/{SERVICE_NAME}.pid"
_token_missing_logged = False


def check_single_instance():
    pid = os.getpid()
    try:
        with open(_pid_file, 'r') as f:
            existing_pid = int(f.read().strip())
        if existing_pid != pid and os.path.exists(f'/proc/{existing_pid}'):
            log.warning(f"Another instance running: {existing_pid}. Exiting.")
            return False
    except (FileNotFoundError, ValueError):
        pass
    with open(_pid_file, 'w') as f:
        f.write(str(pid))
    return True


def remove_pid_file():
    try:
        os.remove(_pid_file)
    except OSError:
        pass


def get_runpod_token() -> Optional[str]:
    return os.environ.get("RUNPOD_API_TOKEN")


def get_ssh_key_path() -> str:
    env_path = os.environ.get(SSH_KEY_ENV_VAR)
    if env_path and os.path.exists(env_path):
        return env_path
    if os.path.exists(SSH_KEY_DEFAULT_PATH):
        return SSH_KEY_DEFAULT_PATH
    return os.environ.get("SSH_KEY_PATH", "/home/ubuntu/.ssh/id_rsa")


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=10
        )
        if resp.status_code == 200:
            result = resp.json()
            return result.get("ok", False)
        return False
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        if resp.status_code == 200:
            result = resp.json()
            return result.get("ok", False)
        return False
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        if resp.status_code == 200:
            result = resp.json()
            return result.get("rows", [])
        return []
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ensure_mesh_events_table():
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mesh_events (
            event_id INTEGER DEFAULT NEXTVAL('mesh_events_seq'),
            event_type VARCHAR NOT NULL,
            severity VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            target VARCHAR,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def emit_mesh_event(event_type: str, severity: str, detail: str, target: Optional[str] = None):
    global _last_pod_status
    ws_write("mesh_events", [{
        "event_type": event_type,
        "severity": severity,
        "source": SERVICE_NAME,
        "target": target or "",
        "detail": detail
    }])
    log.info(f"MESH_EVENT: [{severity}] {event_type} - {detail}")


def send_heartbeat():
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": get_utc_now_iso()
    }])


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def query_runpod_graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    token = get_runpod_token()
    if not token:
        return {"error": "No token"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        resp = requests.post(
            RUNPOD_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            log.error(f"RunPod API error: {resp.status_code} - {resp.text}")
            return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        log.error(f"RunPod request failed: {e}")
        return {"error": str(e)}


def get_all_pods() -> List[Dict[str, Any]]:
    query = """
    query {
        myself {
            pods {
                id
                name
                status
                runtime {
                    ports {
                        privatePort
                        publicPort
                    }
                    ip
                }
                machine {
                    gpuDisplayName
                }
                startedAt
                costTotal
                costSaved
                userId
            }
        }
    }
    """
    result = query_runpod_graphql(query)
    if "errors" in result:
        log.error(f"GraphQL errors: {result['errors']}")
        return []
    try:
        return result.get("data", {}).get("myself", {}).get("pods", []) or []
    except Exception as e:
        log.error(f"Failed to parse pods: {e}")
        return []


def filter_sft_pods(pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = []
    for pod in pods:
        name = pod.get("name", "").lower()
        if "sft" in name or "zomesh" in name:
            filtered.append(pod)
    return filtered


def pick_latest_pod(pods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pods:
        return None
    latest = None
    latest_time = None
    for pod in pods:
        started = pod.get("startedAt")
        if started:
            try:
                dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                if latest_time is None or dt > latest_time:
                    latest_time = dt
                    latest = pod
            except Exception:
                continue
    return latest


def extract_ssh_port(pod: Dict[str, Any]) -> Optional[int]:
    runtime = pod.get("runtime", {})
    ports = runtime.get("ports", [])
    for port_info in ports:
        if port_info.get("privatePort") == 22:
            return port_info.get("publicPort")
    return None


def get_credits_remaining(pod: Dict[str, Any]) -> Optional[float]:
    try:
        cost_total = pod.get("costTotal", 0) or 0
        cost_saved = pod.get("costSaved", 0) or 0
        return float(cost_total - cost_saved)
    except (ValueError, TypeError):
        return None


def build_session_json(pod: Dict[str, Any], polled_at: str) -> Dict[str, Any]:
    runtime = pod.get("runtime", {})
    machine = pod.get("machine", {})
    ssh_port = extract_ssh_port(pod)
    credits = get_credits_remaining(pod)

    return {
        "pod_id": pod.get("id", ""),
        "pod_name": pod.get("name", ""),
        "public_ip": runtime.get("ip", ""),
        "ssh_port": ssh_port,
        "gpu_type": machine.get("gpuDisplayName", ""),
        "status": pod.get("status", ""),
        "started_at": pod.get("startedAt", ""),
        "credits_remaining_usd": credits,
        "ssh_key_name": "zocomputerRPMay",
        "ssh_key_path": get_ssh_key_path(),
        "polled_at": polled_at
    }


def write_session_file(session_data: Dict[str, Any]) -> bool:
    temp_file = f"{SESSION_FILE}.tmp"
    try:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(temp_file, 'w') as f:
            json.dump(session_data, f, indent=2)
        os.rename(temp_file, SESSION_FILE)
        log.info(f"Wrote session file: {SESSION_FILE}")
        return True
    except Exception as e:
        log.error(f"Failed to write session file: {e}")
        try:
            os.remove(temp_file)
        except OSError:
            pass
        return False


def load_previous_session() -> Optional[Dict[str, Any]]:
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load previous session: {e}")
        return None


_last_pod_status: Optional[str] = None


def handle_status_transitions(current_status: str, pod_name: str, pod_id: str):
    global _last_pod_status
    if _last_pod_status == "RUNNING" and current_status != "RUNNING":
        emit_mesh_event(
            event_type="runpod_pod_lost",
            severity="error",
            detail=f"Pod '{pod_name}' ({pod_id}) changed from RUNNING to {current_status}",
            target=pod_id
        )
    _last_pod_status = current_status


def handle_credits_warning(credits: Optional[float], pod_name: str, pod_id: str):
    if credits is not None and credits < CREDITS_WARNING_THRESHOLD:
        emit_mesh_event(
            event_type="runpod_credits_low",
            severity="warning",
            detail=f"Pod '{pod_name}' ({pod_id}) credits low: ${credits:.2f} remaining",
            target=pod_id
        )


def run_cycle():
    global _token_missing_logged, _last_pod_status

    token = get_runpod_token()
    if not token:
        if not _token_missing_logged:
            log.warning("RUNPOD_API_TOKEN not set. Exiting cleanly.")
            _token_missing_logged = True
        return False

    log.info("Querying RunPod API for pods...")
    pods = get_all_pods()
    log.info(f"Found {len(pods)} total pods")

    sft_pods = filter_sft_pods(pods)
    log.info(f"Filtered to {len(sft_pods)} SFT/ZoMesh pods")

    if not sft_pods:
        log.warning("No SFT/ZoMesh pods found")
        empty_session = {
            "pod_id": None,
            "pod_name": None,
            "public_ip": None,
            "ssh_port": None,
            "gpu_type": None,
            "status": "NO_SFT_PODS",
            "started_at": None,
            "credits_remaining_usd": None,
            "ssh_key_name": "zocomputerRPMay",
            "ssh_key_path": get_ssh_key_path(),
            "polled_at": get_utc_now_iso()
        }
        write_session_file(empty_session)
        return True

    latest_pod = pick_latest_pod(sft_pods)
    if not latest_pod:
        log.error("Failed to pick latest pod")
        return False

    polled_at = get_utc_now_iso()
    session_data = build_session_json(latest_pod, polled_at)
    write_session_file(session_data)

    current_status = session_data.get("status", "")
    handle_status_transitions(current_status, session_data.get("pod_name", ""), session_data.get("pod_id", ""))
    handle_credits_warning(session_data.get("credits_remaining_usd"), session_data.get("pod_name", ""), session_data.get("pod_id", ""))

    log.info(f"Session updated: {session_data.get('pod_name')} ({session_data.get('pod_id')}) - {current_status}")

    return True


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}. Shutting down.")
    remove_pid_file()
    exit(0)


def run():
    global _token_missing_logged

    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        return

    log.info(f"Starting {SERVICE_NAME}")
    ensure_mesh_events_table()

    token = get_runpod_token()
    if not token:
        log.warning("RUNPOD_API_TOKEN not set. Exiting cleanly.")
        _token_missing_logged = True
        return

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    log.info("Heartbeat thread started")

    prev_session = load_previous_session()
    if prev_session:
        global _last_pod_status
        _last_pod_status = prev_session.get("status")

    send_heartbeat()

    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Error in run cycle: {e}")

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()