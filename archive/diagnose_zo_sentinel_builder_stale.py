import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

LOGGING_CONFIG = {
    "level": logging.INFO,
    "format": "%(asctime)s | DIAGNOSTIC-ZO-SENTINEL-BUILDER | %(levelname)-7s | %(message)s",
    "datefmt": "%Y-%m-%dT%H:%M:%S",
}

logger = logging.getLogger(__name__)
logging.basicConfig(**LOGGING_CONFIG)


def get_zo_sentinel_builder_health(write_service_url: str) -> Optional[dict[str, Any]]:
    try:
        response = requests.post(
            f"{write_service_url}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "zo_sentinel_builder"
                },
                "wait": True
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success") and result.get("data"):
            rows = result["data"]
            if rows:
                return rows[0]
        return None
    except requests.RequestException as e:
        logger.error(f"Failed to query service_health: {e}")
        return None


def get_recent_error_logs(write_service_url: str, service_name: str, lookback_seconds: int = 3600) -> list[dict]:
    try:
        response = requests.post(
            f"{write_service_url}/write",
            json={
                "table": "audit_log",
                "rows": {
                    "target_server_id": service_name
                },
                "wait": True
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        errors = []
        if result.get("success") and result.get("data"):
            cutoff = datetime.now(timezone.utc).timestamp() - lookback_seconds
            for row in result["data"]:
                timestamp = row.get("timestamp", "")
                if timestamp:
                    try:
                        row_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                        if row_time >= cutoff and "ERROR" in str(row.get("log_level", "")):
                            errors.append(row)
                    except (ValueError, TypeError):
                        continue
        return errors
    except requests.RequestException as e:
        logger.error(f"Failed to query audit_log: {e}")
        return []


def compute_staleness_gap(last_heartbeat: str) -> float:
    try:
        hb_time = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        gap = (now - hb_time).total_seconds()
        return gap
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to parse last_heartbeat '{last_heartbeat}': {e}")
        return -1


def format_duration(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}h{minutes}m{secs}s"


def run():
    logger.info("Starting diagnostic inspection for stale zo_sentinel_builder service")
    
    write_service_url = "http://127.0.0.1:8772"
    service_name = "zo_sentinel_builder"
    staleness_threshold = 600.0
    
    health = get_zo_sentinel_builder_health(write_service_url)
    
    if health is None:
        logger.warning("zo_sentinel_builder not found in service_health table")
        print("DIAGNOSTIC RESULT: SERVICE NOT FOUND")
        sys.exit(1)
    
    last_heartbeat = health.get("last_heartbeat", "UNKNOWN")
    logger.info(f"Current last_heartbeat: {last_heartbeat}")
    
    gap_seconds = compute_staleness_gap(last_heartbeat)
    
    if gap_seconds < 0:
        logger.error("Cannot determine staleness - heartbeat parsing failed")
        print("DIAGNOSTIC RESULT: HEARTBEAT PARSE FAILURE")
        sys.exit(2)
    
    gap_formatted = format_duration(gap_seconds)
    logger.info(f"Staleness gap: {gap_seconds:.1f}s ({gap_formatted})")
    logger.info(f"Threshold: {staleness_threshold}s")
    
    is_stale = gap_seconds > staleness_threshold
    
    print("\n" + "=" * 60)
    print("ZO-SENTINEL BUILDER STALENESS DIAGNOSTIC REPORT")
    print("=" * 60)
    print(f"Service Name:        {service_name}")
    print(f"Last Heartbeat:     {last_heartbeat}")
    print(f"Gap Duration:       {gap_formatted} ({gap_seconds:.1f}s)")
    print(f"Threshold:          {staleness_threshold}s")
    print(f"Status:             {'STALE' if is_stale else 'HEALTHY'}")
    print("=" * 60)
    
    error_logs = get_recent_error_logs(write_service_url, service_name, lookback_seconds=3600)
    
    print(f"\nRecent Error Logs (last 1h): {len(error_logs)} entries")
    if error_logs:
        for idx, log in enumerate(error_logs[:10], 1):
            print(f"  [{idx}] timestamp={log.get('timestamp','N/A')} level={log.get('log_level','N/A')}")
            print(f"       message={log.get('message', log.get('action', 'N/A'))[:100]}")
    else:
        print("  No error logs found in recent window")
    
    print("=" * 60)
    if is_stale:
        print(f"DIAGNOSTIC RESULT: STALE (gap {gap_formatted} exceeds {staleness_threshold}s threshold)")
        logger.warning(f"Service is stale: gap={gap_seconds:.1f}s > threshold={staleness_threshold}s")
    else:
        print(f"DIAGNOSTIC RESULT: HEALTHY (gap within threshold)")
        logger.info("Service heartbeat is within acceptable range")
    
    print("=" * 60)


if __name__ == "__main__":
    run()