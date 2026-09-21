import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "scoring_frequency_scoring_consumer"
SERVICE_PORT = None
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(SERVICE_NAME)

_process_start_time = time.time()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_db_path():
    return "/home/workspace/Datasets/zo-sentinel/sentinel.db"


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_SERVICE_URL


def get_execute_url():
    return EXECUTE_SERVICE_URL


def ws_write(table, rows):
    url = get_write_url()
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql):
    url = get_query_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_execute(sql):
    url = get_execute_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.info(f"Stale PID file found (process {old_pid} not running), removing")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    log.info(f"PID {os.getpid()} written to {PID_FILE}")


def remove_pid_file():
    try:
        Path(PID_FILE).unlink()
    except FileNotFoundError:
        pass


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status="running", meta=None):
    meta = meta or {}
    meta["uptime_seconds"] = int(time.time() - _process_start_time)
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": str(meta),
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def ensure_tables():
    create_scoring_frequency_table_sql = """
    CREATE TABLE IF NOT EXISTS scoring_frequency (
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        scoring_count INTEGER DEFAULT 0,
        last_scored_at TIMESTAMPTZ,
        first_scored_at TIMESTAMPTZ,
        frequency_per_hour REAL DEFAULT 0.0,
        computed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (server_id, signal_type)
    )
    """
    create_scoring_events_table_sql = """
    CREATE TABLE IF NOT EXISTS scoring_frequency_events (
        event_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        scored_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    create_scoring_frequency_summary_sql = """
    CREATE TABLE IF NOT EXISTS scoring_frequency_summary (
        summary_id VARCHAR PRIMARY KEY,
        total_servers INTEGER DEFAULT 0,
        total_events INTEGER DEFAULT 0,
        avg_frequency_per_hour REAL DEFAULT 0.0,
        max_frequency_per_hour REAL DEFAULT 0.0,
        min_frequency_per_hour REAL DEFAULT 0.0,
        computed_at TIMESTAMPTZ NOT NULL
    )
    """
    try:
        ws_execute(create_scoring_frequency_table_sql)
        ws_execute(create_scoring_events_table_sql)
        ws_execute(create_scoring_frequency_summary_sql)
        log.info("Scoring frequency tables ensured")
    except Exception as e:
        log.error(f"Failed to ensure tables: {e}")
        raise


def compute_deterministic_id(*parts):
    import hashlib
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_recent_scoring_events(limit=1000):
    sql = f"""
    SELECT 
        server_id,
        signal_type,
        scored_at
    FROM mcp_signal_scores
    WHERE scored_at IS NOT NULL
    ORDER BY scored_at DESC
    LIMIT {limit}
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.warning(f"Failed to query recent scoring events: {e}")
        return []


def get_existing_frequency_records():
    sql = """
    SELECT 
        server_id,
        signal_type,
        scoring_count,
        last_scored_at,
        first_scored_at,
        frequency_per_hour
    FROM scoring_frequency
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.warning(f"Failed to query existing frequency records: {e}")
        return []


def compute_frequency_metrics(events):
    if not events:
        return {}
    server_signal_events = {}
    for event in events:
        key = (event.get("server_id"), event.get("signal_type"))
        if key not in server_signal_events:
            server_signal_events[key] = []
        server_signal_events[key].append(event)
    
    frequency_map = {}
    for (server_id, signal_type), evts in server_signal_events.items():
        if not evts:
            continue
        scored_times = []
        for evt in evts:
            ts_str = evt.get("scored_at")
            if ts_str:
                try:
                    if ts_str.endswith("Z"):
                        ts_str = ts_str[:-1] + "+00:00"
                    scored_times.append(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
                except (ValueError, TypeError):
                    pass
        if len(scored_times) < 2:
            first_ts = scored_times[0] if scored_times else datetime.now(timezone.utc)
            last_ts = scored_times[0] if scored_times else datetime.now(timezone.utc)
            hours_span = max((last_ts - first_ts).total_seconds() / 3600, 0.1)
        else:
            scored_times.sort()
            first_ts = scored_times[0]
            last_ts = scored_times[-1]
            hours_span = max((last_ts - first_ts).total_seconds() / 3600, 0.1)
        freq_per_hour = len(scored_times) / hours_span if hours_span > 0 else 0.0
        frequency_map[(server_id, signal_type)] = {
            "scoring_count": len(scored_times),
            "first_scored_at": first_ts.isoformat(),
            "last_scored_at": last_ts.isoformat(),
            "frequency_per_hour": round(freq_per_hour, 4),
        }
    return frequency_map


def upsert_frequency_record(server_id, signal_type, metrics, computed_at):
    existing_sql = f"""
    SELECT scoring_count, frequency_per_hour 
    FROM scoring_frequency 
    WHERE server_id = '{server_id}' AND signal_type = '{signal_type}'
    """
    try:
        existing = ws_query(existing_sql)
    except Exception:
        existing = []
    
    if existing:
        old_count = existing[0].get("scoring_count", 0)
        old_freq = existing[0].get("frequency_per_hour", 0.0)
        new_count = old_count + metrics.get("scoring_count", 0)
        freq = metrics.get("frequency_per_hour", 0.0)
        update_sql = f"""
        UPDATE scoring_frequency
        SET scoring_count = {new_count},
            last_scored_at = '{metrics.get('last_scored_at')}',
            frequency_per_hour = {freq},
            computed_at = '{computed_at}'
        WHERE server_id = '{server_id}' AND signal_type = '{signal_type}'
        """
        try:
            ws_execute(update_sql)
        except Exception as e:
            log.warning(f"Failed to update frequency record: {e}")
    else:
        row = {
            "server_id": server_id,
            "signal_type": signal_type,
            "scoring_count": metrics.get("scoring_count", 0),
            "last_scored_at": metrics.get("last_scored_at"),
            "first_scored_at": metrics.get("first_scored_at"),
            "frequency_per_hour": metrics.get("frequency_per_hour", 0.0),
            "computed_at": computed_at,
        }
        try:
            ws_write("scoring_frequency", [row])
        except Exception as e:
            log.warning(f"Failed to insert frequency record: {e}")


def record_scoring_event(server_id, signal_type, scored_at):
    event_id = compute_deterministic_id(server_id, signal_type, scored_at)
    existing_sql = f"SELECT event_id FROM scoring_frequency_events WHERE event_id = '{event_id}'"
    try:
        existing = ws_query(existing_sql)
        if existing:
            return
    except Exception:
        pass
    row = {
        "event_id": event_id,
        "server_id": server_id,
        "signal_type": signal_type,
        "scored_at": scored_at,
        "created_at": utc_now_iso(),
    }
    try:
        ws_write("scoring_frequency_events", [row])
    except Exception as e:
        log.warning(f"Failed to record scoring event: {e}")


def compute_summary(computed_at):
    freq_sql = """
    SELECT 
        COUNT(DISTINCT server_id) as total_servers,
        SUM(scoring_count) as total_events,
        AVG(frequency_per_hour) as avg_frequency,
        MAX(frequency_per_hour) as max_frequency,
        MIN(frequency_per_hour) as min_frequency
    FROM scoring_frequency
    """
    try:
        stats = ws_query(freq_sql)
        if stats:
            s = stats[0]
            summary_id = compute_deterministic_id("scoring_frequency_summary", computed_at)
            summary = {
                "summary_id": summary_id,
                "total_servers": s.get("total_servers", 0) or 0,
                "total_events": s.get("total_events", 0) or 0,
                "avg_frequency_per_hour": round(s.get("avg_frequency", 0.0) or 0.0, 4),
                "max_frequency_per_hour": round(s.get("max_frequency", 0.0) or 0.0, 4),
                "min_frequency_per_hour": round(s.get("min_frequency", 0.0) or 0.0, 4),
                "computed_at": computed_at,
            }
            ws_write("scoring_frequency_summary", [summary])
            log.info(f"Summary updated: {summary['total_servers']} servers, {summary['total_events']} events")
    except Exception as e:
        log.warning(f"Failed to compute summary: {e}")


def cycle():
    log.info("Starting scoring frequency cycle")
    computed_at = utc_now_iso()
    events = get_recent_scoring_events(limit=1000)
    log.info(f"Retrieved {len(events)} scoring events")
    if events:
        for evt in events:
            server_id = evt.get("server_id")
            signal_type = evt.get("signal_type")
            scored_at = evt.get("scored_at")
            if server_id and signal_type and scored_at:
                record_scoring_event(server_id, signal_type, scored_at)
    frequency_map = compute_frequency_metrics(events)
    log.info(f"Computed frequency for {len(frequency_map)} server-signal pairs")
    for (server_id, signal_type), metrics in frequency_map.items():
        upsert_frequency_record(server_id, signal_type, metrics, computed_at)
    compute_summary(computed_at)
    log.info("Scoring frequency cycle completed")


def run():
    log.info(f"Starting {SERVICE_NAME} daemon")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_tables()
    POLL_SECS = int(os.environ.get("SCORING_FREQUENCY_POLL_SECS", 300))
    log.info(f"Poll interval: {POLL_SECS} seconds")
    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Cycle failed: {e}")
        try:
            send_heartbeat(status="running", meta={"poll_secs": POLL_SECS})
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()