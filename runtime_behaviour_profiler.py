#!/usr/bin/env python3
"""
runtime_behaviour_profiler.py -- ZO-SENTINEL runtime behaviour profiling daemon.
Profiles MCP server runtime behaviour from mesh_events, detects anomalies.
"""
import os
import time
import json
import logging
import hashlib
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from collections import defaultdict

import requests

log = logging.getLogger(__name__)

SERVICE_NAME = "runtime_behaviour_profiler"
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
QUERY_SERVICE_URL = os.getenv("QUERY_SERVICE_URL", "http://127.0.0.1:8772/query")
EXECUTE_SERVICE_URL = os.getenv("EXECUTE_SERVICE_URL", "http://127.0.0.1:8772/execute")
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 3600
OUTPUT_DIR = "/home/workspace/zo_sentinel"

RATE_ABUSE_THRESHOLD = 100
EVASION_HOUR_START = 0
EVASION_HOUR_END = 6
EVASION_THRESHOLD = 0.90
BULK_EXFILTRATION_MULTIPLIER = 10
AFTER_HOURS_START = 2
AFTER_HOURS_END = 4


@dataclass
class ServerProfile:
    server_id: str
    call_frequency: float = 0.0
    unique_callers: int = 0
    total_calls: int = 0
    time_window_hours: float = 24.0
    after_hours_calls: int = 0
    total_after_hours_hours: int = 6
    payload_sizes: List[int] = field(default_factory=list)
    payload_size_median: float = 0.0
    payload_size_variance: float = 0.0
    call_hours: Dict[int, int] = field(default_factory=dict)
    caller_ids: set = field(default_factory=set)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


@dataclass
class AnomalyAlert:
    server_id: str
    anomaly_type: str
    severity: str
    evidence: str
    details: Dict[str, Any]
    detected_at: datetime


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def send_heartbeat() -> bool:
    return ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "pid": os.getpid()
    }])


def check_single_instance() -> bool:
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    my_pid = os.getpid()
    
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Instance already running with PID {old_pid}")
                return False
            except OSError:
                log.info(f"Stale PID file found (PID {old_pid}), taking ownership")
        except (ValueError, IOError) as e:
            log.warning(f"Could not read PID file: {e}")
    
    try:
        with open(pid_file, "w") as f:
            f.write(str(my_pid))
        return True
    except IOError as e:
        log.error(f"Could not create PID file: {e}")
        return False


def remove_pid_file() -> None:
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except OSError:
        pass


def fetch_recent_events() -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        id,
        event_type,
        server_id,
        caller_id,
        payload_size,
        created_at
    FROM mesh_events
    WHERE event_type IN ('assessment_requested', 'mcp_tool_called', 'mcp_verdict_update')
    AND created_at > now() - INTERVAL '24 hours'
    ORDER BY created_at DESC
    """
    return ws_query(sql)


def build_server_profiles(events: List[Dict[str, Any]]) -> Dict[str, ServerProfile]:
    profiles: Dict[str, ServerProfile] = defaultdict(lambda: ServerProfile(server_id=""))
    
    for event in events:
        server_id = event.get("server_id", "unknown")
        if not server_id or server_id == "unknown":
            continue
        
        profile = profiles[server_id]
        profile.server_id = server_id
        profile.total_calls += 1
        
        caller_id = event.get("caller_id")
        if caller_id:
            profile.caller_ids.add(str(caller_id))
        
        payload_size = event.get("payload_size", 0)
        if payload_size and isinstance(payload_size, (int, float)):
            profile.payload_sizes.append(int(payload_size))
        
        created_at = event.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif not isinstance(created_at, datetime):
                continue
            
            hour = created_at.hour
            profile.call_hours[hour] = profile.call_hours.get(hour, 0) + 1
            
            if profile.first_seen is None or created_at < profile.first_seen:
                profile.first_seen = created_at
            if profile.last_seen is None or created_at > profile.last_seen:
                profile.last_seen = created_at
            
            if EVASION_HOUR_START <= hour < EVASION_HOUR_END:
                profile.after_hours_calls += 1
    
    now = datetime.now(timezone.utc)
    
    for server_id, profile in profiles.items():
        profile.unique_callers = len(profile.caller_ids)
        
        if profile.first_seen and profile.last_seen:
            delta = profile.last_seen - profile.first_seen
            profile.time_window_hours = max(delta.total_seconds() / 3600, 0.1)
        
        if profile.time_window_hours > 0:
            profile.call_frequency = profile.total_calls / profile.time_window_hours
        
        if profile.payload_sizes:
            profile.payload_size_median = statistics.median(profile.payload_sizes)
            if len(profile.payload_sizes) > 1:
                profile.payload_size_variance = statistics.variance(profile.payload_sizes)
    
    return profiles


def detect_rate_abuse(profile: ServerProfile) -> Optional[AnomalyAlert]:
    if profile.call_frequency > RATE_ABUSE_THRESHOLD:
        return AnomalyAlert(
            server_id=profile.server_id,
            anomaly_type="rate_abuse",
            severity="high",
            evidence=f"Call frequency {profile.call_frequency:.1f}/hr exceeds threshold {RATE_ABUSE_THRESHOLD}",
            details={
                "call_frequency": profile.call_frequency,
                "total_calls": profile.total_calls,
                "time_window_hours": profile.time_window_hours,
                "unique_callers": profile.unique_callers,
                "threshold": RATE_ABUSE_THRESHOLD
            },
            detected_at=datetime.now(timezone.utc)
        )
    return None


def detect_evasion_pattern(profile: ServerProfile) -> Optional[AnomalyAlert]:
    if profile.total_calls < 10:
        return None
    
    after_hours_ratio = profile.after_hours_calls / profile.total_calls
    
    if after_hours_ratio >= EVASION_THRESHOLD:
        return AnomalyAlert(
            server_id=profile.server_id,
            anomaly_type="evasion_pattern",
            severity="medium",
            evidence=f"{after_hours_ratio*100:.1f}% of calls occur during 0:00-6:00 UTC (threshold: {EVASION_THRESHOLD*100:.0f}%)",
            details={
                "after_hours_ratio": after_hours_ratio,
                "after_hours_calls": profile.after_hours_calls,
                "total_calls": profile.total_calls,
                "call_hours_distribution": dict(profile.call_hours),
                "threshold": EVASION_THRESHOLD
            },
            detected_at=datetime.now(timezone.utc)
        )
    
    suspicious_2am_4am_ratio = 0
    suspicious_hours_calls = sum(profile.call_hours.get(h, 0) for h in range(AFTER_HOURS_START, AFTER_HOURS_END))
    if profile.total_calls > 0:
        suspicious_2am_4am_ratio = suspicious_hours_calls / profile.total_calls
    
    if suspicious_2am_4am_ratio > 0.8 and profile.total_calls >= 5:
        return AnomalyAlert(
            server_id=profile.server_id,
            anomaly_type="evasion_pattern",
            severity="medium",
            evidence=f"{suspicious_2am_4am_ratio*100:.1f}% of calls occur during 2:00-4:00 UTC (suspicious narrow window)",
            details={
                "narrow_window_ratio": suspicious_2am_4am_ratio,
                "suspicious_hours_calls": suspicious_hours_calls,
                "total_calls": profile.total_calls,
                "call_hours_distribution": dict(profile.call_hours)
            },
            detected_at=datetime.now(timezone.utc)
        )
    
    return None


def detect_bulk_exfiltration(profile: ServerProfile) -> Optional[AnomalyAlert]:
    if not profile.payload_sizes or profile.payload_size_median == 0:
        return None
    
    max_payload = max(profile.payload_sizes)
    ratio = max_payload / profile.payload_size_median
    
    if ratio > BULK_EXFILTRATION_MULTIPLIER:
        return AnomalyAlert(
            server_id=profile.server_id,
            anomaly_type="bulk_exfiltration_attempt",
            severity="high",
            evidence=f"Max payload size {max_payload} bytes is {ratio:.1f}x median (threshold: {BULK_EXFILTRATION_MULTIPLIER}x)",
            details={
                "max_payload_size": max_payload,
                "median_payload_size": profile.payload_size_median,
                "payload_ratio": ratio,
                "payload_sizes_count": len(profile.payload_sizes),
                "payload_size_variance": profile.payload_size_variance
            },
            detected_at=datetime.now(timezone.utc)
        )
    
    consistent_large = all(size > profile.payload_size_median * 2 for size in profile.payload_sizes)
    if consistent_large and profile.payload_size_median > 100000 and len(profile.payload_sizes) >= 5:
        return AnomalyAlert(
            server_id=profile.server_id,
            anomaly_type="bulk_exfiltration_attempt",
            severity="medium",
            evidence=f"Consistently large payloads (median {profile.payload_size_median:.0f} bytes, all > 2x baseline)",
            details={
                "consistent_large_payloads": True,
                "median_payload_size": profile.payload_size_median,
                "payload_sizes": profile.payload_sizes
            },
            detected_at=datetime.now(timezone.utc)
        )
    
    return None


def analyze_temporal_stability(profiles: Dict[str, ServerProfile]) -> Dict[str, float]:
    stability_scores = {}
    
    for server_id, profile in profiles.items():
        if profile.total_calls < 5:
            stability_scores[server_id] = 0.5
            continue
        
        hour_distribution = profile.call_hours
        active_hours = len(hour_distribution)
        
        if active_hours == 0:
            stability_scores[server_id] = 0.0
            continue
        
        peak_hour_count = max(hour_distribution.values()) if hour_distribution else 1
        spread = active_hours / 24.0
        regularity = (peak_hour_count / profile.total_calls) if profile.total_calls > 0 else 0
        
        stability = min(1.0, spread * 0.5 + regularity * 0.5)
        stability_scores[server_id] = stability
    
    return stability_scores


def calculate_community_signal(profile: ServerProfile) -> float:
    score = 0.5
    
    if profile.unique_callers > 1:
        score += min(0.2, profile.unique_callers * 0.02)
    
    if profile.call_frequency > 10 and profile.call_frequency <= RATE_ABUSE_THRESHOLD:
        score += 0.1
    elif profile.call_frequency > RATE_ABUSE_THRESHOLD:
        score -= 0.3
    
    after_hours_ratio = profile.after_hours_calls / profile.total_calls if profile.total_calls > 0 else 0
    if after_hours_ratio > 0.5:
        score -= min(0.3, after_hours_ratio * 0.3)
    
    if profile.payload_sizes:
        if profile.payload_size_median > 1000000:
            score -= 0.2
        elif profile.payload_size_median > 100000:
            score -= 0.1
    
    return max(0.0, min(1.0, score))


def write_threat_associations(anomalies: List[AnomalyAlert]) -> bool:
    if not anomalies:
        return True
    
    rows = []
    for anomaly in anomalies:
        rows.append({
            "server_id": anomaly.server_id,
            "threat_type": anomaly.anomaly_type,
            "severity": anomaly.severity,
            "evidence": anomaly.evidence,
            "details": json.dumps(anomaly.details),
            "reported_at": datetime.now(timezone.utc).isoformat()
        })
    
    return ws_write("mcp_threat_associations", rows)


def write_signal_scores(profiles: Dict[str, ServerProfile], stability_scores: Dict[str, float]) -> bool:
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    
    for server_id, profile in profiles.items():
        stability = stability_scores.get(server_id, 0.5)
        rows.append({
            "server_id": server_id,
            "signal_name": "temporal_stability",
            "score": stability,
            "evidence": f"Based on {profile.total_calls} calls over {profile.time_window_hours:.1f} hours, {len(profile.call_hours)} active hours",
            "scored_at": now
        })
        
        community_signal = calculate_community_signal(profile)
        rows.append({
            "server_id": server_id,
            "signal_name": "community_signal",
            "score": community_signal,
            "evidence": f"Unique callers: {profile.unique_callers}, call freq: {profile.call_frequency:.1f}/hr, after-hours ratio: {profile.after_hours_calls/profile.total_calls if profile.total_calls > 0 else 0:.2f}",
            "scored_at": now
        })
    
    if not rows:
        return True
    
    return ws_write("mcp_signal_scores", rows)


def ensure_threat_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence    TEXT,
        severity    VARCHAR,
        details     TEXT,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)


def ensure_signal_scores_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score       REAL,
        evidence    TEXT,
        scored_at   TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)


def write_behaviour_profile_md(profiles: Dict[str, ServerProfile], anomalies: List[AnomalyAlert]) -> None:
    now = datetime.now(timezone.utc)
    lines = [
        "# Runtime Behaviour Profile\n",
        f"**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n",
        f"**Service:** {SERVICE_NAME}\n",
        f"**Poll Interval:** {POLL_INTERVAL}s\n",
        "\n## Profile Summary\n",
        f"- **Servers Profiled:** {len(profiles)}\n",
        f"- **Anomalies Detected:** {len(anomalies)}\n",
        "\n## Profiling Methodology\n",
        "- **Data Source:** mesh_events table (last 24 hours)\n",
        "- **Event Types:** assessment_requested, mcp_tool_called, mcp_verdict_update\n",
        "- **Metrics Collected:**\n",
        "  - call_frequency (calls/hour)\n",
        "  - unique_callers\n",
        "  - time_of_day_distribution\n",
        "  - payload_size_variance\n",
        "\n## Anomaly Thresholds\n",
        f"- **rate_abuse:** call_frequency > {RATE_ABUSE_THRESHOLD}/hr\n",
        f"- **evasion_pattern:** >{EVASION_THRESHOLD*100:.0f}% calls between 0:00-6:00 UTC\n",
        f"- **bulk_exfiltration_attempt:** payload_size > {BULK_EXFILTRATION_MULTIPLIER}x median\n",
    ]
    
    if anomalies:
        lines.append("\n## Detected Anomalies\n")
        for i, anomaly in enumerate(anomalies, 1):
            lines.append(f"\n### {i}. {anomaly.anomaly_type.upper()} - {anomaly.server_id}\n")
            lines.append(f"- **Severity:** {anomaly.severity}\n")
            lines.append(f"- **Evidence:** {anomaly.evidence}\n")
            lines.append(f"- **Details:**\n")
            for key, value in anomaly.details.items():
                lines.append(f"  - {key}: {value}\n")
            lines.append(f"- **Detected At:** {anomaly.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    else:
        lines.append("\n## Detected Anomalies\n")
        lines.append("*No anomalies detected in this cycle.*\n")
    
    if profiles:
        lines.append("\n## Server Profiles\n")
        for server_id in sorted(profiles.keys()):
            profile = profiles[server_id]
            lines.append(f"\n### {server_id}\n")
            lines.append(f"- **Total Calls:** {profile.total_calls}\n")
            lines.append(f"- **Call Frequency:** {profile.call_frequency:.2f} calls/hour\n")
            lines.append(f"- **Unique Callers:** {profile.unique_callers}\n")
            lines.append(f"- **After-Hours Calls (0-6 UTC):** {profile.after_hours_calls} ({profile.after_hours_calls/profile.total_calls*100 if profile.total_calls > 0 else 0:.1f}%)\n")
            if profile.payload_sizes:
                lines.append(f"- **Payload Size Median:** {profile.payload_size_median:.0f} bytes\n")
                lines.append(f"- **Payload Size Max:** {max(profile.payload_sizes)} bytes\n")
            if profile.call_hours:
                lines.append(f"- **Active Hours:** {sorted(profile.call_hours.keys())}\n")
            lines.append(f"- **Time Window:** {profile.time_window_hours:.1f} hours\n")
    
    output_path = os.path.join(OUTPUT_DIR, "BEHAVIOUR_PROFILE.md")
    try:
        with open(output_path, "w") as f:
            f.writelines(lines)
        log.info(f"Wrote behaviour profile to {output_path}")
    except IOError as e:
        log.error(f"Failed to write behaviour profile: {e}")


def run_cycle() -> Tuple[int, int]:
    log.info("Starting behaviour profiling cycle")
    
    events = fetch_recent_events()
    log.info(f"Fetched {len(events)} recent events")
    
    if not events:
        log.info("No events to profile, skipping cycle")
        return 0, 0
    
    profiles = build_server_profiles(events)
    log.info(f"Built profiles for {len(profiles)} servers")
    
    anomalies: List[AnomalyAlert] = []
    
    for server_id, profile in profiles.items():
        rate_abuse = detect_rate_abuse(profile)
        if rate_abuse:
            anomalies.append(rate_abuse)
        
        evasion = detect_evasion_pattern(profile)
        if evasion:
            anomalies.append(evasion)
        
        exfiltration = detect_bulk_exfiltration(profile)
        if exfiltration:
            anomalies.append(exfiltration)
    
    log.info(f"Detected {len(anomalies)} anomalies")
    
    stability_scores = analyze_temporal_stability(profiles)
    
    write_threat_associations(anomalies)
    write_signal_scores(profiles, stability_scores)
    
    write_behaviour_profile_md(profiles, anomalies)
    
    return len(profiles), len(anomalies)


def heartbeat_loop() -> None:
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log.error("Could not acquire single instance lock")
        return
    
    try:
        ensure_threat_table()
        ensure_signal_scores_table()
    except Exception as e:
        log.error(f"Table setup failed: {e}")
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Cycle failed: {e}")
        
        log.info(f"Sleeping for {POLL_INTERVAL} seconds")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()