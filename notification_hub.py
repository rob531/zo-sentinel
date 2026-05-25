#!/usr/bin/env python3
"""
notification_hub.py -- ZO-SENTINEL Notification Hub
Centralises all outbound notifications with best-effort delivery.
No daemon - standalone utility module.
"""
import requests
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# Constants
NOTIFY_API_URL = "http://api.zo.computer/zo/notify"
NOTIFY_TIMEOUT = 8
ALERT_QUEUE_FILE = "ALERT_QUEUE.md"

# Severity levels
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"
SEVERITY_CRITICAL = "CRITICAL"

# Global counters
send_count = 0
failure_count = 0


def _log_success(method: str, subject: str) -> None:
    """Log successful notification."""
    global send_count
    send_count += 1
    log.info(f"[NOTIFICATION_HUB] Success: {method} - {subject}")


def _log_failure(method: str, subject: str, error: str) -> None:
    """Log failed notification."""
    global failure_count
    failure_count += 1
    log.warning(f"[NOTIFICATION_HUB] Failure: {method} - {subject} - {error}")


def _queue_alert(subject: str, body: str, severity: str, alert_type: str) -> None:
    """Write alert to queue file as fallback."""
    try:
        timestamp = datetime.utcnow().isoformat() + "Z"
        with open(ALERT_QUEUE_FILE, "a") as f:
            f.write(f"---\n")
            f.write(f"timestamp: {timestamp}\n")
            f.write(f"type: {alert_type}\n")
            f.write(f"severity: {severity}\n")
            f.write(f"subject: {subject}\n")
            f.write(f"body: |\n")
            for line in body.split('\n'):
                f.write(f"  {line}\n")
            f.write(f"---\n\n")
        log.info(f"[NOTIFICATION_HUB] Queued alert to {ALERT_QUEUE_FILE}")
    except Exception as e:
        log.error(f"[NOTIFICATION_HUB] Failed to write to queue: {e}")


def _call_notify_api(payload: dict) -> bool:
    """Make HTTP call to notification API."""
    try:
        response = requests.post(
            NOTIFY_API_URL,
            json=payload,
            timeout=NOTIFY_TIMEOUT,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ZO-SENTINEL/notification_hub"
            }
        )
        if response.status_code in (200, 201, 202):
            return True
        log.warning(f"[NOTIFICATION_HUB] API returned status {response.status_code}")
        return False
    except requests.exceptions.Timeout:
        log.warning(f"[NOTIFICATION_HUB] API timeout after {NOTIFY_TIMEOUT}s")
        return False
    except requests.exceptions.ConnectionError as e:
        log.warning(f"[NOTIFICATION_HUB] API connection error: {e}")
        return False
    except Exception as e:
        log.warning(f"[NOTIFICATION_HUB] API unexpected error: {e}")
        return False


def send_alert(subject: str, body: str, severity: str = SEVERITY_HIGH) -> bool:
    """
    Send a generic alert notification.
    
    Args:
        subject: Alert subject line
        body: Alert body content
        severity: Alert severity (HIGH, MEDIUM, LOW, CRITICAL)
    
    Returns:
        bool: True if sent successfully or queued, False only on critical failure
    """
    payload = {
        "type": "alert",
        "subject": subject,
        "body": body,
        "severity": severity,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": "ZO-SENTINEL"
    }
    
    success = _call_notify_api(payload)
    
    if success:
        _log_success("send_alert", subject)
        return True
    else:
        _log_failure("send_alert", subject, "API unavailable")
        _queue_alert(subject, body, severity, "alert")
        return True


def send_build_failure(task: str, description: str, reason: str) -> bool:
    """
    Send build failure notification formatted for Claude chat ingestion.
    
    Args:
        task: Name of the failed task
        description: Task description
        reason: Failure reason
    
    Returns:
        bool: True if sent successfully or queued, False only on critical failure
    """
    subject = f"BUILD FAILURE: {task}"
    
    body_lines = [
        f"## Build Failure Detected",
        f"",
        f"**Task:** {task}",
        f"**Description:** {description}",
        f"**Reason:** {reason}",
        f"**Timestamp:** {datetime.utcnow().isoformat()}Z",
        f"",
        f"Please review the failure and take appropriate action."
    ]
    body = "\n".join(body_lines)
    
    payload = {
        "type": "build_failure",
        "subject": subject,
        "body": body,
        "severity": SEVERITY_HIGH,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": "ZO-SENTINEL",
        "task": task,
        "description": description,
        "reason": reason
    }
    
    success = _call_notify_api(payload)
    
    if success:
        _log_success("send_build_failure", task)
        return True
    else:
        _log_failure("send_build_failure", task, "API unavailable")
        _queue_alert(subject, body, SEVERITY_HIGH, "build_failure")
        return True


def send_daily_digest(content: str) -> bool:
    """
    Send daily digest notification.
    
    Args:
        content: Digest content (markdown formatted)
    
    Returns:
        bool: True if sent successfully or queued, False only on critical failure
    """
    subject = "ZO-SENTINEL Daily Digest"
    
    payload = {
        "type": "daily_digest",
        "subject": subject,
        "body": content,
        "severity": SEVERITY_LOW,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": "ZO-SENTINEL"
    }
    
    success = _call_notify_api(payload)
    
    if success:
        _log_success("send_daily_digest", subject)
        return True
    else:
        _log_failure("send_daily_digest", subject, "API unavailable")
        _queue_alert(subject, content, SEVERITY_LOW, "daily_digest")
        return True


def notify_high_risk(server_name: str, verdict: str, trust_score: float) -> bool:
    """
    Send high-risk server notification.
    
    Args:
        server_name: Name of the high-risk server
        verdict: Verdict assignment (MALICIOUS, SUSPICIOUS, etc.)
        trust_score: Trust score of the server
    
    Returns:
        bool: True if sent successfully or queued, False only on critical failure
    """
    subject = f"HIGH RISK: {server_name}"
    
    body_lines = [
        f"## High Risk Server Detected",
        f"",
        f"**Server:** {server_name}",
        f"**Verdict:** {verdict}",
        f"**Trust Score:** {trust_score:.2f}",
        f"**Timestamp:** {datetime.utcnow().isoformat()}Z",
        f"",
        f"Immediate attention required."
    ]
    body = "\n".join(body_lines)
    
    severity = SEVERITY_CRITICAL if trust_score < 0.2 else SEVERITY_HIGH
    
    payload = {
        "type": "high_risk",
        "subject": subject,
        "body": body,
        "severity": severity,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": "ZO-SENTINEL",
        "server_name": server_name,
        "verdict": verdict,
        "trust_score": trust_score
    }
    
    success = _call_notify_api(payload)
    
    if success:
        _log_success("notify_high_risk", server_name)
        return True
    else:
        _log_failure("notify_high_risk", server_name, "API unavailable")
        _queue_alert(subject, body, severity, "high_risk")
        return True


def get_stats() -> dict:
    """
    Get notification hub statistics.
    
    Returns:
        dict: Statistics with send_count and failure_count
    """
    return {
        "send_count": send_count,
        "failure_count": failure_count,
        "success_rate": send_count / (send_count + failure_count) if (send_count + failure_count) > 0 else 1.0
    }


def reset_stats() -> None:
    """Reset send and failure counters."""
    global send_count, failure_count
    send_count = 0
    failure_count = 0
    log.info("[NOTIFICATION_HUB] Stats reset")


def get_queued_alerts() -> list:
    """
    Read and return queued alerts from ALERT_QUEUE.md.
    
    Returns:
        list: List of queued alert dictionaries
    """
    alerts = []
    try:
        if not os.path.exists(ALERT_QUEUE_FILE):
            return alerts
            
        with open(ALERT_QUEUE_FILE, "r") as f:
            content = f.read()
            
        current_alert = {}
        for line in content.split("\n"):
            if line.strip() == "---":
                if current_alert:
                    alerts.append(current_alert)
                current_alert = {}
            elif ": " in line:
                key, value = line.split(": ", 1)
                current_alert[key.strip()] = value.strip()
        
        if current_alert:
            alerts.append(current_alert)
            
    except Exception as e:
        log.error(f"[NOTIFICATION_HUB] Error reading queue: {e}")
    
    return alerts


def clear_queue() -> bool:
    """
    Clear the alert queue file.
    
    Returns:
        bool: True if cleared successfully
    """
    try:
        if os.path.exists(ALERT_QUEUE_FILE):
            os.remove(ALERT_QUEUE_FILE)
        log.info("[NOTIFICATION_HUB] Queue cleared")
        return True
    except Exception as e:
        log.error(f"[NOTIFICATION_HUB] Error clearing queue: {e}")
        return False


# Import os for file operations
import os

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    log.info("[NOTIFICATION_HUB] Testing module...")
    
    result = send_alert("Test Alert", "This is a test notification", SEVERITY_MEDIUM)
    log.info(f"send_alert result: {result}")
    
    result = send_build_failure("test_task", "Test description", "Test reason")
    log.info(f"send_build_failure result: {result}")
    
    result = send_daily_digest("# Daily Digest\n\nTest content")
    log.info(f"send_daily_digest result: {result}")
    
    result = notify_high_risk("test_server", "SUSPICIOUS", 0.15)
    log.info(f"notify_high_risk result: {result}")
    
    log.info(f"Stats: {get_stats()}")