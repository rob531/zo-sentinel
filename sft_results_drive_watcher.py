import os
import time
import json
import logging
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import sys
sys.path.insert(0, '/home/workspace')

import requests

SERVICE_NAME = "sft_results_drive_watcher"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
QUERY_SERVICE_URL = os.environ.get("QUERY_SERVICE_URL", "http://127.0.0.1:8772/query")
EXECUTE_SERVICE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://127.0.0.1:8772/execute")

DRIVE_SFT_RESULTS_FOLDER_ID = os.environ.get("DRIVE_SFT_RESULTS_FOLDER_ID", "")
POLL_SECS = 60
HEARTBEAT_INTERVAL = 60
RUNS_BASE_DIR = Path("/home/workspace/shared/sft/runs")

BACKOFF_BASE = 5
BACKOFF_MAX = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            get_write_url(),
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.warning(f"ws_write failed: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(
            get_query_url(),
            json={"sql": sql},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        return []
    except Exception as e:
        log.warning(f"ws_query failed: {e}")
        return []


def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": now}])


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            log.info("Stale PID file removed")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_drive_client():
    """Get the Google Drive client using the canonical ZoComputer pattern.
    
    This follows the established pattern from world_agent and other Drive-aware
    daemons in the ZoComputer codebase.
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/home/workspace/config/google-service-account.json")
        if not os.path.exists(creds_path):
            log.error(f"Google credentials not found at {creds_path}")
            return None
        
        scopes = ['https://www.googleapis.com/auth/drive.readonly']
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
        drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        return drive_service
    except ImportError as e:
        log.warning(f"Google API client not available: {e}")
        return None
    except Exception as e:
        log.error(f"Failed to initialize Drive client: {e}")
        return None


def list_folder_files(drive_service, folder_id: str) -> List[Dict[str, Any]]:
    """List files in a Google Drive folder matching run_complete_*.json pattern."""
    query = f"'{folder_id}' in parents and name contains 'run_complete_' and name like '%.json' and trashed=false"
    results = []
    page_token = None
    
    while True:
        try:
            response = drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, size, modifiedTime)',
                pageToken=page_token,
                pageSize=100
            ).execute()
            
            files = response.get('files', [])
            results.extend(files)
            
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            log.error(f"Failed to list folder files: {e}")
            raise
    
    return results


def download_file(drive_service, file_id: str, local_path: Path) -> bool:
    """Download a file from Google Drive to a local path atomically."""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        
        with tempfile.NamedTemporaryFile(delete=False, dir=local_path.parent) as tmp:
            downloader = MediaIoBaseDownload(tmp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        
        temp_path = tmp.name
        shutil.move(temp_path, local_path)
        return True
    except Exception as e:
        log.error(f"Failed to download file {file_id}: {e}")
        return False


def ensure_directory(path: Path):
    """Ensure directory exists with proper permissions."""
    path.mkdir(parents=True, exist_ok=True)


def read_manifest_manifest(drive_service, file_id: str) -> Optional[Dict[str, Any]]:
    """Read and parse a run_complete JSON manifest."""
    try:
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload
        
        request = drive_service.files().get_media(fileId=file_id)
        buffer = BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        buffer.seek(0)
        return json.loads(buffer.read().decode('utf-8'))
    except Exception as e:
        log.error(f"Failed to read manifest {file_id}: {e}")
        return None


def ingest_run(drive_service, folder_id: str, manifest_file: Dict[str, Any], backoff: int) -> int:
    """Ingest a single run from its manifest. Returns backoff on failure, 0 on success."""
    manifest_id = manifest_file['id']
    manifest_name = manifest_file['name']
    
    manifest = read_manifest_manifest(drive_service, manifest_id)
    if not manifest:
        return min(backoff * 2, BACKOFF_MAX) if backoff > 0 else BACKOFF_BASE
    
    run_id = manifest.get('run_id', '')
    if not run_id:
        log.warning(f"Manifest {manifest_name} missing run_id, skipping")
        return 0
    
    run_dir = RUNS_BASE_DIR / run_id
    ingested_marker = run_dir / "INGESTED"
    
    if ingested_marker.exists():
        log.debug(f"Run {run_id} already ingested, skipping")
        return 0
    
    ensure_directory(run_dir)
    
    artifacts = manifest.get('artifacts', [])
    total_bytes = 0
    artifact_count = 0
    
    for artifact in artifacts:
        artifact_name = artifact.get('name', '')
        artifact_drive_id = artifact.get('drive_id', '')
        expected_size = artifact.get('size', 0)
        
        if not artifact_name or not artifact_drive_id:
            log.warning(f"Artifact missing name or drive_id in {manifest_name}")
            continue
        
        local_path = run_dir / artifact_name
        
        if not download_file(drive_service, artifact_drive_id, local_path):
            log.error(f"Failed to download artifact {artifact_name} for run {run_id}")
            return BACKOFF_BASE
        
        actual_size = local_path.stat().st_size
        if expected_size > 0 and actual_size != expected_size:
            log.error(f"Size mismatch for {artifact_name}: expected {expected_size}, got {actual_size}")
            return BACKOFF_BASE
        
        total_bytes += actual_size
        artifact_count += 1
    
    with open(ingested_marker, 'w') as f:
        f.write(f"ingested_at: {utc_now_iso()}\n")
        f.write(f"manifest: {manifest_name}\n")
        f.write(f"artifact_count: {artifact_count}\n")
    
    ws_write("mesh_events", [{
        "severity": "info",
        "event_type": "sft_run_ingested",
        "run_id": run_id,
        "artifact_count": artifact_count,
        "total_bytes": total_bytes,
        "ingested_at": utc_now_iso()
    }])
    
    log.info(f"Successfully ingested run {run_id}: {artifact_count} artifacts, {total_bytes} bytes")
    return 0


def poll_drive_folder(backoff: int) -> int:
    """Poll the Drive folder for new run manifests. Returns next backoff value."""
    folder_id = os.environ.get("DRIVE_SFT_RESULTS_FOLDER_ID", "")
    
    if not folder_id:
        if backoff == -1:
            log.info("DRIVE_SFT_RESULTS_FOLDER_ID not configured, sleeping for 60s")
            backoff = POLL_SECS
        return backoff
    
    drive_service = get_drive_client()
    if not drive_service:
        log.warning("Drive client unavailable, using backoff")
        return min(backoff * 2, BACKOFF_MAX) if backoff > 0 else BACKOFF_BASE
    
    try:
        manifest_files = list_folder_files(drive_service, folder_id)
        
        for manifest_file in manifest_files:
            new_backoff = ingest_run(drive_service, folder_id, manifest_file, backoff)
            if new_backoff > 0:
                return new_backoff
        
        return 0
    except Exception as e:
        log.error(f"Error polling Drive folder: {e}")
        return min(backoff * 2, BACKOFF_MAX) if backoff > 0 else BACKOFF_BASE


def run():
    """Main daemon loop."""
    import signal
    
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log.error("Failed to acquire lock, exiting")
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_directory(RUNS_BASE_DIR)
    
    backoff = -1
    last_heartbeat = time.time()
    folder_configured = os.environ.get("DRIVE_SFT_RESULTS_FOLDER_ID", "")
    
    if not folder_configured:
        log.info(f"DRIVE_SFT_RESULTS_FOLDER_ID not set. Will poll once per {POLL_SECS}s when configured.")
    
    while True:
        try:
            current_time = time.time()
            
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = current_time
            
            backoff = poll_drive_folder(backoff)
            
            sleep_time = backoff if backoff > 0 else POLL_SECS
            log.debug(f"Sleeping for {sleep_time}s (backoff={backoff})")
            time.sleep(sleep_time)
            
            if backoff == 0:
                backoff = -1
                
        except Exception as e:
            log.error(f"Unexpected error in main loop: {e}")
            time.sleep(POLL_SECS)
    
    remove_pid_file()


if __name__ == '__main__':
    run()