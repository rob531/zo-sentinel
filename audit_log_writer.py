import requests
import uuid
from datetime import datetime, timezone

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def write_audit_event(action: str, target_server_id: str, actor: str, details: dict) -> bool:
    """Write an audit event to the audit_log table via write_service.
    
    All admin-write actions (exemption create, attestation revoke, manual verdict override)
    must go through this function per security contract section 7.
    
    Args:
        action: The action being performed (e.g., 'exemption_created', 'attestation_revoked', 'verdict_override')
        target_server_id: The server ID being acted upon
        actor: The admin/system performing the action
        details: Additional context as a dict (converted to JSON)
    
    Returns:
        True on success, False on write_service failure
    """
    request_id = str(uuid.uuid4())
    
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "target_server_id": target_server_id,
        "actor": actor,
        "details": details,
        "request_id": request_id
    }
    
    payload = {
        "table": "audit_log",
        "rows": [event],
        "wait": True
    }
    
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result.get("ok", False)
    except requests.exceptions.RequestException as e:
        print(f"Failed to write audit event: {e}")
        return False


def log_exemption_created(server_id: str, admin_email: str, reason: str, expires_at: str = None) -> bool:
    """Log an exemption creation event."""
    return write_audit_event(
        action="exemption_created",
        target_server_id=server_id,
        actor=admin_email,
        details={"reason": reason, "expires_at": expires_at}
    )


def log_attestation_revoked(server_id: str, admin_email: str, reason: str) -> bool:
    """Log an attestation revocation event."""
    return write_audit_event(
        action="attestation_revoked",
        target_server_id=server_id,
        actor=admin_email,
        details={"reason": reason}
    )


def log_verdict_override(server_id: str, admin_email: str, old_verdict: str, new_verdict: str, reason: str) -> bool:
    """Log a manual verdict override event."""
    return write_audit_event(
        action="verdict_override",
        target_server_id=server_id,
        actor=admin_email,
        details={
            "old_verdict": old_verdict,
            "new_verdict": new_verdict,
            "reason": reason
        }
    )


if __name__ == "__main__":
    success = write_audit_event(
        action="test_event",
        target_server_id="test-123",
        actor="test@example.com",
        details={"test": True}
    )
    print(f"Test audit event write: {'SUCCESS' if success else 'FAILED'}")