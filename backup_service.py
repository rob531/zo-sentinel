#!/usr/bin/env python3
"""
backup_service.py -- ZO-SENTINEL backup daemon.
Weekly: export critical tables to JSON + gzip in /home/workspace/zo_sentinel/backups/.
"""
import os
import json
import gzip
import logging
import time
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List
import threading

import requests

from db_utils import ws_query, ws_write, ws_execute, ws_heartbeat, get_unscored_servers

# Constants
SERVICE_NAME = "backup_service"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
BACKUP_DIR = "/home/workspace/zo_sentinel/backups"
TABLES_TO_BACKUP = [
    "mcp_server_registry",
    "mcp_signal_scores",
    "mcp_attestations",
    "mcp_decisions"
]
HEARTBEAT_INTERVAL = 300
CHECK_INTERVAL = 86400
MAX_WEEKLY_BACKUPS = 4

log = logging.getLogger(__name__)


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            if old_pid and old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    log.warning(f"Another instance already running (PID {old_pid})")
                    return False
                except OSError:
                    pass
        except (ValueError, IOError):
            pass
    try:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
        return True
    except IOError as e:
        log.error(f"Failed to create PID file: {e}")
        return False


def send_heartbeat():
    """Send service heartbeat."""
    try:
        ws_heartbeat(SERVICE_NAME)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def backup_table(table_name: str, backup_path: str) -> int:
    """Export table to JSON + gzip file."""
    try:
        rows = ws_query(table_name)
        row_count = len(rows)
        json_path = os.path.join(backup_path, f"{table_name}.json")
        gz_path = f"{json_path}.gz"
        
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            json.dump(rows, f)
        
        file_size = os.path.getsize(gz_path)
        log.info(f"Backed up {table_name}: {row_count} rows -> {gz_path} ({file_size} bytes)")
        return row_count
    except Exception as e:
        log.error(f"Failed to backup table {table_name}: {e}")
        return 0


def cleanup_old_backups(current_date: str):
    """Delete backups older than the last 4 weekly ones."""
    try:
        weekly_dirs = []
        if not os.path.exists(BACKUP_DIR):
            return
        
        for entry in os.listdir(BACKUP_DIR):
            full_path = os.path.join(BACKUP_DIR, entry)
            if os.path.isdir(full_path) and entry != current_date:
                try:
                    dir_date = datetime.strptime(entry, "%Y-%m-%d")
                    weekly_dirs.append((dir_date, full_path))
                except ValueError:
                    log.warning(f"Skipping non-date directory: {entry}")
        
        weekly_dirs.sort(key=lambda x: x[0], reverse=True)
        
        if len(weekly_dirs) > MAX_WEEKLY_BACKUPS:
            for dir_date, dir_path in weekly_dirs[MAX_WEEKLY_BACKUPS:]:
                try:
                    shutil.rmtree(dir_path)
                    log.info(f"Deleted old backup: {dir_path}")
                except OSError as e:
                    log.error(f"Failed to delete old backup {dir_path}: {e}")
    except Exception as e:
        log.error(f"Cleanup failed: {e}")


def write_backup_manifest(backup_path: str, row_counts: Dict[str, int], table_files: Dict[str, str]):
    """Write backup manifest with metadata and statistics."""
    try:
        manifest = {
            "created": datetime.utcnow().isoformat(),
            "backup_date": os.path.basename(backup_path),
            "tables": {}
        }
        
        for table_name, count in row_counts.items():
            gz_path = os.path.join(backup_path, f"{table_name}.json.gz")
            file_size = os.path.getsize(gz_path) if os.path.exists(gz_path) else 0
            manifest["tables"][table_name] = {
                "row_count": count,
                "file_size": file_size,
                "file": f"{table_name}.json.gz"
            }
        
        manifest["total_rows"] = sum(row_counts.values())
        manifest["total_size"] = sum(t["file_size"] for t in manifest["tables"].values())
        
        manifest_path = os.path.join(backup_path, "BACKUP_MANIFEST.md")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("# ZO-SENTINEL Backup Manifest\n\n")
            f.write(f"**Created:** {manifest['created']}\n")
            f.write(f"**Backup Date:** {manifest['backup_date']}\n\n")
            f.write("## Tables\n\n")
            f.write("| Table | Rows | Size (bytes) | File |\n")
            f.write("|-------|------|--------------|-------|\n")
            for table_name, info in manifest["tables"].items():
                f.write(f"| {table_name} | {info['row_count']} | {info['file_size']} | {info['file']} |\n")
            f.write(f"\n## Summary\n\n")
            f.write(f"- **Total Rows:** {manifest['total_rows']}\n")
            f.write(f"- **Total Size:** {manifest['total_size']} bytes\n")
            f.write(f"- **Tables Backed Up:** {len(manifest['tables'])}\n")
        
        manifest_json_path = os.path.join(backup_path, "manifest.json")
        with gzip.open(f"{manifest_json_path}.gz", "wt", encoding="utf-8") as f:
            json.dump(manifest, f)
        
        log.info(f"Written manifest: {manifest_path}")
    except Exception as e:
        log.error(f"Failed to write manifest: {e}")


def record_backup_complete(backup_path: str, row_counts: Dict[str, int], total_size: int):
    """Record backup completion in mesh_events table."""
    try:
        ws_write("mesh_events", {
            "event_type": "backup_complete",
            "service": SERVICE_NAME,
            "backup_path": backup_path,
            "row_counts": json.dumps(row_counts),
            "total_size": total_size,
            "backup_date": os.path.basename(backup_path)
        })
        log.info(f"Recorded backup_complete event to mesh_events")
    except Exception as e:
        log.error(f"Failed to record backup_complete event: {e}")


def perform_backup() -> bool:
    """Execute the weekly backup operation."""
    try:
        current_date = datetime.utcnow().strftime("%Y-%m-%d")
        backup_path = os.path.join(BACKUP_DIR, current_date)
        
        os.makedirs(backup_path, exist_ok=True)
        log.info(f"Starting weekly backup to {backup_path}")
        
        row_counts: Dict[str, int] = {}
        table_files: Dict[str, str] = {}
        
        for table in TABLES_TO_BACKUP:
            count = backup_table(table, backup_path)
            row_counts[table] = count
            table_files[table] = f"{table}.json.gz"
        
        write_backup_manifest(backup_path, row_counts, table_files)
        
        total_size = sum(
            os.path.getsize(os.path.join(backup_path, f"{t}.json.gz"))
            for t in TABLES_TO_BACKUP
            if os.path.exists(os.path.join(backup_path, f"{t}.json.gz"))
        )
        
        record_backup_complete(backup_path, row_counts, total_size)
        
        cleanup_old_backups(current_date)
        
        log.info(f"Weekly backup completed successfully: {backup_path}")
        log.info(f"Backed up {sum(row_counts.values())} total rows across {len(row_counts)} tables")
        return True
        
    except Exception as e:
        log.error(f"Weekly backup failed: {e}")
        return False


def is_sunday() -> bool:
    """Check if today is Sunday (Python weekday() returns 6 for Sunday)."""
    return datetime.utcnow().weekday() == 6


def is_backup_due() -> bool:
    """Check if weekly backup is due (Sunday and not already done)."""
    if not is_sunday():
        return False
    
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    today_backup = os.path.join(BACKUP_DIR, current_date)
    
    if os.path.exists(today_backup):
        manifest_path = os.path.join(today_backup, "BACKUP_MANIFEST.md")
        if os.path.exists(manifest_path):
            log.debug(f"Backup already completed today: {today_backup}")
            return False
    
    return True


def heartbeat_loop():
    """Continuously send heartbeat signals."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main daemon loop for backup service."""
    if not check_single_instance():
        log.error("Another backup_service instance is running. Exiting.")
        return
    
    log.info(f"Starting {SERVICE_NAME} daemon...")
    log.info(f"Backup directory: {BACKUP_DIR}")
    log.info(f"Tables to backup: {TABLES_TO_BACKUP}")
    log.info(f"Check interval: {CHECK_INTERVAL}s, Max weekly backups: {MAX_WEEKLY_BACKUPS}")
    
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    log.info("Heartbeat thread started")
    
    last_backup_date = None
    
    try:
        while True:
            try:
                if is_backup_due():
                    current_date = datetime.utcnow().strftime("%Y-%m-%d")
                    if current_date != last_backup_date:
                        log.info(f"Triggering weekly backup (Sunday)")
                        success = perform_backup()
                        if success:
                            last_backup_date = current_date
                        else:
                            log.error("Backup failed, will retry next check")
                    else:
                        log.debug("Backup already attempted for this Sunday")
                else:
                    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    weekday = datetime.utcnow().weekday()
                    log.debug(f"[{current_time}] Not Sunday (weekday={weekday}), backup not due")
                
            except Exception as e:
                log.error(f"Error in main loop: {e}")
            
            time.sleep(CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        log.info(f"Received shutdown signal for {SERVICE_NAME}")
    finally:
        pid_file = f"/tmp/{SERVICE_NAME}.pid"
        if os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except OSError:
                pass
        log.info(f"{SERVICE_NAME} daemon stopped")


if __name__ == "__main__":
    run()