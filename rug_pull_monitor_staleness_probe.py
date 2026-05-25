import logging
import requests
import sys
from datetime import datetime, timezone

SERVICE_NAME = "rug_pull_monitor_staleness_probe"
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_PATH = "/home/workspace/logs/rug_pull_monitor_staleness_probe.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_PATH)]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    payload = {"sql": sql, "wait": True}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except requests.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"ws_write failed: {e}")
        return False


def parse_iso_to_utc(iso_str: str) -> datetime:
    iso_str = iso_str.replace('Z', '+00:00')
    if iso_str.endswith('+00:00'):
        return datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(iso_str).astimezone(timezone.utc)


def format_timedelta(seconds: float) -> str:
    td = abs(seconds)
    if td < 60:
        return f"{td:.1f}s"
    elif td < 3600:
        mins = td / 60
        return f"{mins:.1f}m"
    elif td < 86400:
        hours = td / 3600
        return f"{hours:.1f}h"
    else:
        days = td / 86400
        return f"{days:.1f}d"


def assess_severity(staleness_seconds: float) -> str:
    if staleness_seconds < 3600:
        return "NOMINAL"
    elif staleness_seconds < 7200:
        return "ELEVATED"
    elif staleness_seconds < 86400:
        return "WARNING"
    elif staleness_seconds < 172800:
        return "CRITICAL"
    else:
        return "STALE_EXTREME"


def diagnose_rug_pull_monitor():
    sql = "SELECT service_name, status, last_heartbeat FROM service_health WHERE service_name = 'rug_pull_monitor'"
    rows = ws_query(sql)
    
    if not rows:
        return None
    
    row = rows[0]
    service_name = row.get("service_name", "UNKNOWN")
    status = row.get("status", "UNKNOWN")
    heartbeat_raw = row.get("last_heartbeat", "")
    
    current_utc = datetime.now(timezone.utc)
    heartbeat_dt = parse_iso_to_utc(heartbeat_raw)
    staleness_seconds = (current_utc - heartbeat_dt).total_seconds()
    
    record_age_seconds = (current_utc - heartbeat_dt).total_seconds()
    
    diag = {
        "service_name": service_name,
        "status": status,
        "heartbeat_raw": heartbeat_raw,
        "heartbeat_iso": heartbeat_dt.isoformat(),
        "current_time_iso": current_utc.isoformat(),
        "staleness_seconds": staleness_seconds,
        "staleness_human": format_timedelta(staleness_seconds),
        "severity": assess_severity(staleness_seconds),
        "record_age_seconds": record_age_seconds,
        "record_age_human": format_timedelta(record_age_seconds)
    }
    
    return diag


def main():
    logger.info("Starting rug_pull_monitor staleness diagnostic probe")
    
    diag = diagnose_rug_pull_monitor()
    
    if diag is None:
        logger.error("No service_health record found for rug_pull_monitor")
        print("FAILURE: No service_health record for rug_pull_monitor")
        sys.exit(1)
    
    logger.info(f"Diagnostic complete: service={diag['service_name']} status={diag['status']} staleness={diag['staleness_seconds']}s severity={diag['severity']}")
    
    print("\n" + "=" * 70)
    print("RUG_PULL_MONITOR STALENESS DIAGNOSTIC REPORT")
    print("=" * 70)
    print(f"Service Name:      {diag['service_name']}")
    print(f"Current Status:   {diag['status']}")
    print(f"Last Heartbeat:   {diag['heartbeat_iso']}")
    print(f"Current Time:     {diag['current_time_iso']}")
    print(f"Staleness:        {diag['staleness_human']} ({diag['staleness_seconds']:.1f} seconds)")
    print(f"Severity:         {diag['severity']}")
    print(f"Raw Heartbeat:    {diag['heartbeat_raw']}")
    print("=" * 70)
    
    if diag['severity'] == "STALE_EXTREME":
        print("\nWARNING: Service has been stale for >48 hours")
        print("Action: Review service_health table and infra monitoring")
    elif diag['severity'] == "CRITICAL":
        print("\nNOTICE: Service has been stale for >24 hours")
    
    ws_write("service_health", [{
        "service_name": SERVICE_NAME,
        "status": "completed",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "meta": f"staleness_probe_for_rug_pull_monitor:severity={diag['severity']},stale_seconds={diag['staleness_seconds']:.0f}"
    }])
    
    sys.exit(0)


if __name__ == "__main__":
    main()