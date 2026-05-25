#!/usr/bin/env python3
"""
mesh_bridge.py -- ZO-SENTINEL to ZOMesh integration bridge.
Reads mcp_server_registry verdict changes and emits GateObjects to ZOMesh.
Bridges ZO-SENTINEL assessments into the broader ZOMesh governance pipeline.
"""
import os
import time
import json
import logging
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

import requests

log = logging.getLogger(__name__)

SERVICE_NAME = "mesh_bridge"
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
QUERY_SERVICE_URL = os.getenv("QUERY_SERVICE_URL", "http://127.0.0.1:8772/query")
EXECUTE_URL = os.getenv("EXECUTE_URL", "http://127.0.0.1:8772/execute")
MESH_GUARDIAN_URL = os.getenv("MESH_GUARDIAN_URL", "http://127.0.0.1:8888/gate")
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 300
GATE_TOPIC = "RAW_OUTPUT"


@dataclass
class GateObject:
    payload_type: str
    payload: Dict[str, Any]
    source: str = "zo_sentinel"
    timestamp: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"
        if self.correlation_id is None:
            self.correlation_id = hashlib.sha256(
                f"{self.payload_type}{self.timestamp}{os.urandom(8)}".encode()
            ).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Execute failed: {e}")
        return False


def send_heartbeat() -> bool:
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat() + "Z",
        "status": "running"
    })


def check_single_instance() -> bool:
    lock_key = f"{SERVICE_NAME}_lock"
    lock_time = datetime.utcnow().isoformat() + "Z"
    try:
        result = ws_query(f"SELECT lock_value FROM sentinel_locks WHERE lock_key = '{lock_key}'")
        if result:
            lock_value = result[0].get("lock_value")
            if lock_value:
                lock_dt = datetime.fromisoformat(lock_value.replace("Z", "+00:00"))
                if datetime.now() - lock_dt.replace(tzinfo=None) < timedelta(seconds=POLL_INTERVAL * 2):
                    log.warning(f"Another instance of {SERVICE_NAME} is running")
                    return False
        ws_execute(f"""
            INSERT OR REPLACE INTO sentinel_locks (lock_key, lock_value, locked_at)
            VALUES ('{lock_key}', '{lock_time}', '{lock_time}')
        """)
        return True
    except Exception as e:
        log.debug(f"Lock check: {e}")
        try:
            ws_execute("""
                CREATE TABLE IF NOT EXISTS sentinel_locks (
                    lock_key VARCHAR PRIMARY KEY,
                    lock_value VARCHAR,
                    locked_at TIMESTAMPTZ
                )
            """)
            ws_execute(f"""
                INSERT OR REPLACE INTO sentinel_locks (lock_key, lock_value, locked_at)
                VALUES ('{lock_key}', '{lock_time}', '{lock_time}')
            """)
            return True
        except Exception as e2:
            log.error(f"Failed to acquire lock: {e2}")
            return False


def ensure_mesh_events_table() -> bool:
    return ws_execute("""
        CREATE TABLE IF NOT EXISTS mesh_events (
            id BIGINT PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            event_payload TEXT,
            source VARCHAR,
            correlation_id VARCHAR,
            processed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT now(),
            processed_at TIMESTAMPTZ
        )
    """)


def emit_verdict_gate(server_id: str, verdict: str, trust_score: Optional[float], reasoning: Optional[str]) -> bool:
    gate_obj = GateObject(
        payload_type="mcp_verdict_update",
        payload={
            "server_id": server_id,
            "verdict": verdict,
            "trust_score": trust_score,
            "reasoning": reasoning,
            "source_system": "zo_sentinel",
            "emitted_at": datetime.utcnow().isoformat() + "Z"
        },
        source="zo_sentinel"
    )

    try:
        resp = requests.post(
            MESH_GUARDIAN_URL,
            json={
                "topic": GATE_TOPIC,
                "gate_object": gate_obj.to_dict()
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        log.info(f"Emitted verdict gate for {server_id}: {verdict}")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to emit verdict gate for {server_id}: {e}")
        return emit_to_mesh_events(gate_obj)


def emit_assessment_request_gate(server_id: str, requestor: str, priority: str = "normal") -> bool:
    gate_obj = GateObject(
        payload_type="assessment_requested",
        payload={
            "server_id": server_id,
            "requestor": requestor,
            "priority": priority,
            "source_system": "zo_sentinel",
            "requested_at": datetime.utcnow().isoformat() + "Z"
        },
        source="zo_sentinel"
    )

    try:
        resp = requests.post(
            MESH_GUARDIAN_URL,
            json={
                "topic": GATE_TOPIC,
                "gate_object": gate_obj.to_dict()
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        log.info(f"Emitted assessment request gate for {server_id}")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to emit assessment request gate: {e}")
        return emit_to_mesh_events(gate_obj)


def emit_trust_update_gate(server_id: str, trust_score: float, signals: Dict[str, Any]) -> bool:
    gate_obj = GateObject(
        payload_type="trust_score_update",
        payload={
            "server_id": server_id,
            "trust_score": trust_score,
            "signals": signals,
            "source_system": "zo_sentinel",
            "updated_at": datetime.utcnow().isoformat() + "Z"
        },
        source="zo_sentinel"
    )

    try:
        resp = requests.post(
            MESH_GUARDIAN_URL,
            json={
                "topic": GATE_TOPIC,
                "gate_object": gate_obj.to_dict()
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        log.info(f"Emitted trust update gate for {server_id}: {trust_score}")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to emit trust update gate: {e}")
        return emit_to_mesh_events(gate_obj)


def emit_to_mesh_events(gate_obj: GateObject) -> bool:
    return ws_write("mesh_events", {
        "event_type": gate_obj.payload_type,
        "event_payload": json.dumps(gate_obj.to_dict()),
        "source": gate_obj.source,
        "correlation_id": gate_obj.correlation_id,
        "processed": False,
        "created_at": gate_obj.timestamp
    })


def get_last_emitted_verdicts() -> Dict[str, str]:
    result = ws_query("""
        SELECT server_id, verdict FROM mesh_gate_emitted
        WHERE emitted_at > NOW() - INTERVAL '24 hours'
    """)
    return {row["server_id"]: row["verdict"] for row in result}


def mark_verdict_emitted(server_id: str, verdict: str, correlation_id: str) -> bool:
    return ws_write("mesh_gate_emitted", {
        "server_id": server_id,
        "verdict": verdict,
        "correlation_id": correlation_id,
        "emitted_at": datetime.utcnow().isoformat() + "Z"
    })


def check_verdict_changes() -> List[Dict[str, Any]]:
    last_emitted = get_last_emitted_verdicts()
    servers = ws_query("""
        SELECT server_id, name, verdict, trust_score, verdict_reasoning, last_assessed
        FROM mcp_server_registry
        WHERE verdict IS NOT NULL
        AND last_assessed > NOW() - INTERVAL '1 hour'
        ORDER BY last_assessed DESC
    """)

    changed = []
    for server in servers:
        server_id = server["server_id"]
        current_verdict = server["verdict"]
        if server_id not in last_emitted or last_emitted[server_id] != current_verdict:
            changed.append(server)
    return changed


def subscribe_to_assessments() -> List[Dict[str, Any]]:
    assessment_requests = ws_query("""
        SELECT id, server_id, requestor, priority
        FROM mesh_events
        WHERE event_type = 'assessment_requested'
        AND processed = FALSE
        AND created_at > NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
        LIMIT 50
    """)
    return assessment_requests


def mark_assessment_processed(event_id: int) -> bool:
    return ws_execute(f"""
        UPDATE mesh_events
        SET processed = TRUE, processed_at = '{datetime.utcnow().isoformat()}Z'
        WHERE id = {event_id}
    """)


def bridge_assessment_to_registry(server_id: str, requestor: str) -> bool:
    current = ws_query(f"""
        SELECT id, scan_count FROM mcp_server_registry
        WHERE server_id = '{server_id}'
    """)
    if current:
        scan_count = (current[0].get("scan_count") or 0) + 1
        return ws_execute(f"""
            UPDATE mcp_server_registry
            SET scan_count = {scan_count},
                last_seen = '{datetime.utcnow().isoformat()}Z'
            WHERE server_id = '{server_id}'
        """)
    return False


def create_tables() -> bool:
    tables_ok = ws_execute("""
        CREATE TABLE IF NOT EXISTS mesh_gate_emitted (
            id BIGINT PRIMARY KEY,
            server_id VARCHAR NOT NULL,
            verdict VARCHAR,
            correlation_id VARCHAR,
            emitted_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    ensure_mesh_events_table()
    return tables_ok


def run():
    log.info(f"Starting {SERVICE_NAME} - ZO-SENTINEL to ZOMesh bridge")

    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        return

    if not create_tables():
        log.error("Failed to create required tables. Exiting.")
        return

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    log.info(f"Mesh bridge polling every {POLL_INTERVAL}s")
    processed_count = 0

    while True:
        try:
            verdict_changes = check_verdict_changes()
            for server in verdict_changes:
                success = emit_verdict_gate(
                    server["server_id"],
                    server["verdict"],
                    server.get("trust_score"),
                    server.get("verdict_reasoning")
                )
                if success:
                    mark_verdict_emitted(
                        server["server_id"],
                        server["verdict"],
                        hashlib.sha256(server["server_id"].encode()).hexdigest()[:16]
                    )
                    processed_count += 1

            assessment_requests = subscribe_to_assessments()
            for request in assessment_requests:
                log.info(f"Processing assessment request for {request['server_id']}")
                bridge_assessment_to_registry(request["server_id"], request.get("requestor", "mesh"))
                emit_assessment_request_gate(
                    request["server_id"],
                    request.get("requestor", "mesh"),
                    request.get("priority", "normal")
                )
                mark_assessment_processed(request["id"])
                processed_count += 1

            if processed_count > 0:
                log.info(f"Processed {processed_count} events this cycle")
                processed_count = 0

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log.info(f"Received shutdown signal for {SERVICE_NAME}")
            break
        except Exception as e:
            log.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(30)


def _heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    run()