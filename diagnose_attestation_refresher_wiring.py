import os
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SUPERVISORD_CONF_PATHS = [
    "/etc/supervisor/conf.d/attestation_refresher.conf",
    "/etc/supervisord.conf",
    "/home/workspace/zo_sentinel/supervisord.conf",
]
LOG_DIR = Path("/var/log/zo_sentinel")
LOG_PATHS = [
    LOG_DIR / "attestation_refresher.log",
    Path("/var/log/zo_sentinel/attestation_refresher.log"),
    Path("/home/workspace/zo_sentinel/attestation_refresher.log"),
]
MODULE_PATH = Path("/home/workspace/zo_sentinel/attestation_refresher.py")
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def compute_file_hash(filepath: Path) -> str | None:
    if not filepath.exists():
        return None
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def check_supervisord_registration() -> dict[str, Any]:
    for conf_path in SUPERVISORD_CONF_PATHS:
        path = Path(conf_path)
        if path.exists():
            try:
                content = path.read_text()
                if "attestation_refresher" in content.lower():
                    return {"registered": True, "config_file": str(path)}
            except Exception as e:
                logger.warning(f"Failed to read {path}: {e}")
    return {"registered": False, "config_file": None}


def check_log_files() -> dict[str, Any]:
    for log_path in LOG_PATHS:
        if log_path.exists():
            try:
                stat = log_path.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                return {
                    "exists": True,
                    "path": str(log_path),
                    "size_bytes": size,
                    "mtime": mtime.isoformat()
                }
            except Exception as e:
                logger.warning(f"Failed to stat {log_path}: {e}")
    return {"exists": False, "path": None, "size_bytes": 0, "mtime": None}


def check_module_integrity() -> dict[str, Any]:
    if not MODULE_PATH.exists():
        return {"exists": False, "path": str(MODULE_PATH), "hash": None}
    file_hash = compute_file_hash(MODULE_PATH)
    stat = MODULE_PATH.stat()
    size = stat.st_size
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "exists": True,
        "path": str(MODULE_PATH),
        "hash": file_hash,
        "size_bytes": size,
        "mtime": mtime.isoformat()
    }


def check_heartbeat_status() -> dict[str, Any]:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "query": "SELECT * FROM service_health WHERE service = 'attestation_refresher' ORDER BY last_heartbeat DESC LIMIT 1",
                "wait": True
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("rows", [])
        if rows:
            row = rows[0]
            last_heartbeat = row.get("last_heartbeat")
            if last_heartbeat:
                dt = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
                return {
                    "heartbeat_found": True,
                    "last_heartbeat": last_heartbeat,
                    "age_seconds": age_seconds,
                    "healthy": age_seconds < 300
                }
        return {"heartbeat_found": False, "last_heartbeat": None, "age_seconds": None, "healthy": False}
    except Exception as e:
        logger.error(f"Failed to check heartbeat: {e}")
        return {"heartbeat_found": False, "error": str(e)}


def write_diagnostic_blob(diagnostics: dict[str, Any]) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": "diagnose_attestation_refresher_wiring",
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "meta": json.dumps(diagnostics)
                },
                "wait": True
            },
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to write diagnostic blob: {e}")
        return False


def run() -> dict[str, Any]:
    logger.info("Starting attestation_refresher wiring diagnostics")
    diagnostics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diagnostic_module": "diagnose_attestation_refresher_wiring",
        "module_integrity": check_module_integrity(),
        "supervisord_registration": check_supervisord_registration(),
        "log_files": check_log_files(),
        "heartbeat_status": check_heartbeat_status()
    }
    module_ok = diagnostics["module_integrity"].get("exists", False)
    supervisord_ok = diagnostics["supervisord_registration"].get("registered", False)
    heartbeat_ok = diagnostics["heartbeat_status"].get("healthy", False)
    diagnostics["overall_healthy"] = module_ok and supervisord_ok and heartbeat_ok
    diagnostics["checks_passed"] = sum([module_ok, supervisord_ok, heartbeat_ok])
    diagnostics["checks_total"] = 3
    if diagnostics["overall_healthy"]:
        logger.info("All attestation_refresher wiring checks passed")
    else:
        logger.warning(
            f"Attestation refresher wiring issues: "
            f"module={'OK' if module_ok else 'MISSING'}, "
            f"supervisord={'OK' if supervisord_ok else 'NOT REGISTERED'}, "
            f"heartbeat={'OK' if heartbeat_ok else 'STALE OR MISSING'}"
        )
    write_diagnostic_blob(diagnostics)
    return diagnostics


if __name__ == "__main__":
    results = run()
    print(json.dumps(results, indent=2))
    sys.exit(0 if results.get("overall_healthy") else 1)