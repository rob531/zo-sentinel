# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from __future__ import annotations

import sys
from typing import Any, Callable, Optional
from datetime import datetime, timezone


class ServiceBase:
    """Base class for zo-sentinel service components."""

    def __init__(self, **kwargs: Any) -> None:
        self._config = kwargs
        self._initialized_at = datetime.now(timezone.utc)

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "component": self.__class__.__name__}


class PerspectiveSnapshot(ServiceBase):
    """Snapshot of perspective state for attestation."""

    def __init__(self, perspective_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.perspective_id = perspective_id
        self._state: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "perspective_id": self.perspective_id,
            "state": self._state.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class TargetServer(ServiceBase):
    """Server target for attestation verification."""

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.host = host
        self.port = port

    def check_heartbeat(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "alive": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def get_timestamp() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def validate_config(config: dict[str, Any]) -> bool:
    """Validate service configuration."""
    return isinstance(config, dict)


def emit_signal(
    signal_type: str,
    payload: dict[str, Any],
    *,
    source: Optional[str] = None,
) -> dict[str, Any]:
    """Emit a service signal/event."""
    return {
        "type": signal_type,
        "payload": payload,
        "source": source or "zo_sentinel",
        "emitted_at": get_timestamp(),
    }


def query_mesh_store(query: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Query the ZoComputer mesh store."""
    import urllib.request
    import json

    payload = json.dumps({"query": query, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8772/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e), "query": query}


# Exports
__all__ = [
    "ServiceBase",
    "PerspectiveSnapshot",
    "TargetServer",
    "get_timestamp",
    "validate_config",
    "emit_signal",
    "query_mesh_store",
]


if __name__ == "__main__":
    # Self-test
    try:
        ts = TargetServer(host="localhost", port=8080)
        assert ts.host == "localhost"
        assert ts.port == 8080

        ps = PerspectiveSnapshot(perspective_id="test-001")
        assert ps.perspective_id == "test-001"

        signal = emit_signal("test_signal", {"value": 42})
        assert signal["type"] == "test_signal"
        assert signal["payload"]["value"] == 42

        ts_result = ts.check_heartbeat()
        assert ts_result["alive"] is True

        ps_result = ps.snapshot()
        assert ps_result["perspective_id"] == "test-001"

        assert validate_config({"key": "value"}) is True
        assert validate_config("not a dict") is False

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)