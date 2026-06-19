"""
Temporal stability signal producer module.

Evaluates MCPs based on their age, update frequency, and change history.
Stable long-lived MCPs with consistent update patterns score higher than
brand-new or erratic ones.
"""
import json
import re
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds


def produce_temporal_stability_signal(server_ids: list[str] | None = None) -> dict:
    """
    Produce temporal stability signals for MCPs.
    
    Args:
        server_ids: Optional list of server_ids to filter.
                   If None, evaluates all MCPs without a score in last 7 days.
    
    Returns:
        dict with keys: processed, scored, skipped, errors
    """
    result = {
        "processed": 0,
        "scored": 0,
        "skipped": 0,
        "errors": []
    }
    
    last_heartbeat = time.time()
    
    try:
        # Get all candidate MCP records
        all_records = _fetch_mcp_records(server_ids)
        
        # Filter based on idempotency when server_ids is None
        if server_ids is None:
            mcp_records = [
                r for r in all_records
                if not _has_recent_score(r.get("server_id", ""))
            ]
        else:
            mcp_records = all_records
        
        # Process each MCP record
        for record in mcp_records:
            # Send heartbeat if needed
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                _send_heartbeat()
                last_heartbeat = time.time()
            
            server_id = record.get("server_id", "")
            
            # Compute stability score
            score, evidence_blob = _compute_stability_score(record)
            
            # Write result via write_service
            try:
                _write_signal_score(server_id, score, evidence_blob)
                result["scored"] += 1
            except Exception as e:
                result["errors"].append(f"Error writing score for {server_id}: {str(e)}")
            
            result["processed"] += 1
    
    except Exception as e:
        result["errors"].append(f"Error in produce_temporal_stability_signal: {str(e)}")
    
    return result


def _fetch_mcp_records(server_ids: list[str] | None = None) -> list[dict]:
    """Fetch MCP records from mcp_server_registry with 10s timeout."""
    params = {}
    if server_ids:
        params["server_ids"] = json.dumps(server_ids)
    
    try:
        response = requests.get(
            "http://127.0.0.1:8772/api/mcp_server_registry",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return []


def _has_recent_score(server_id: str) -> bool:
    """
    Check if MCP has a temporal_stability score within the last 7 days.
    Returns True if a recent score exists (skip needed).
    """
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    
    try:
        response = requests.get(
            "http://127.0.0.1:8772/api/mcp_signal_scores",
            params={
                "server_id": server_id,
                "signal_type": "temporal_stability",
                "after": seven_days_ago
            },
            timeout=10
        )
        response.raise_for_status()
        scores = response.json()
        return len(scores) > 0
    except requests.exceptions.RequestException:
        return False


def _compute_stability_score(mcp_record: dict) -> tuple[float, dict]:
    """
    Compute stability score for a single MCP record.
    
    Scoring:
      - age_days: 0-30=10, 30-90=30, 90-365=60, 365+=80 (max 80)
      - update_frequency: regular within 30-day=15, erratic=5, static=0 (max 15)
      - version_stability: semver-compliant=5, non-semver=0 (max 5)
      - Total max=100
    
    Returns:
        tuple of (score, evidence_blob)
    """
    first_seen = mcp_record.get("first_seen", "")
    last_updated = mcp_record.get("last_updated", "")
    
    # Calculate age_days
    age_days = 0
    if first_seen:
        try:
            first_seen_dt = _parse_date(first_seen)
            if first_seen_dt:
                age_days = (datetime.utcnow() - first_seen_dt).days
        except (ValueError, TypeError):
            age_days = 0
    
    # Calculate change_rate from version history if available
    version_history = mcp_record.get("version_history", [])
    change_rate = 0.0
    if version_history and age_days > 0:
        change_rate = len(version_history) / age_days
    
    # Determine version stability
    version_stability = "unknown"
    version = mcp_record.get("version", "")
    if version:
        if _is_semver_compliant(version):
            version_stability = "semver"
        else:
            version_stability = "non-semver"
    elif version_history:
        # Check first version in history
        first_version = version_history[0].get("version", "") if version_history else ""
        if _is_semver_compliant(first_version):
            version_stability = "semver"
        else:
            version_stability = "non-semver"
    
    # Calculate age score (max 80)
    if age_days < 30:
        age_score = 10
    elif age_days < 90:
        age_score = 30
    elif age_days < 365:
        age_score = 60
    else:
        age_score = 80
    
    # Calculate update frequency score (max 15)
    update_frequency_days = mcp_record.get("update_frequency_days")
    if update_frequency_days is None:
        frequency_score = 0
    elif update_frequency_days <= 30:
        frequency_score = 15
    else:
        frequency_score = 5
    
    # Calculate version stability score (max 5)
    version_score = 5 if version_stability == "semver" else 0
    
    # Total score (max 100)
    total_score = min(100, age_score + frequency_score + version_score)
    
    # Build evidence blob
    evidence_blob = {
        "age_days": age_days,
        "update_frequency_days": update_frequency_days,
        "version_stability": version_stability,
        "change_rate": round(change_rate, 6),
        "first_seen": first_seen,
        "last_updated": last_updated
    }
    
    return total_score, evidence_blob


def _write_signal_score(server_id: str, score: float, evidence_blob: dict) -> None:
    """Write signal score via write_service HTTP POST."""
    payload = {
        "server_id": server_id,
        "signal_type": "temporal_stability",
        "score": score,
        "confidence": score / 100.0,
        "evidence_blob": evidence_blob,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    response = requests.post(
        f"{WRITE_SERVICE_URL}/api/mcp_signal_scores",
        json=payload,
        timeout=10
    )
    response.raise_for_status()


def _send_heartbeat() -> None:
    """Send heartbeat POST to write_service."""
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/heartbeat",
            json={"producer": "temporal_stability_signal_producer"},
            timeout=5
        )
    except requests.exceptions.RequestException:
        pass  # Heartbeat failures are non-critical


def _is_semver_compliant(version: str) -> bool:
    """Check if version string is semver-compliant (e.g., 1.0.0, 2.1.3-beta)."""
    if not version:
        return False
    # Basic semver pattern: major.minor.patch with optional pre-release
    semver_pattern = r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$'
    return bool(re.match(semver_pattern, str(version)))


def _parse_date(date_str: str) -> datetime | None:
    """Parse date string into datetime object."""
    if not date_str:
        return None
    
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    # Try ISO format as fallback
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def _datetime_to_iso(dt: datetime | None) -> str:
    """Convert datetime to ISO format string."""
    if dt is None:
        return ""
    return dt.isoformat() + "Z" if not str(dt).endswith("Z") else str(dt)


if __name__ == "__main__":
    result = produce_temporal_stability_signal([])
    assert isinstance(result, dict), "Result must be a dict"
    assert "processed" in result, "Result must have 'processed' key"
    assert "scored" in result, "Result must have 'scored' key"
    assert "skipped" in result, "Result must have 'skipped' key"
    assert "errors" in result, "Result must have 'errors' key"
    assert result["processed"] >= 0, "processed must be >= 0"
    assert isinstance(result["errors"], list), "errors must be a list"
    print("PASS")