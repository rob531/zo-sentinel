# Auto-emitted service package
from __future__ import annotations

import sys
from typing import Any, Optional

# Re-export commonly used types for relative import compatibility
__all__ = [
    "ServiceBase",
    "validate_config",
    "run_self_test",
]


class ServiceBase:
    """Base class for auto-emitted services."""

    service_name: str = "zo_sentinel"
    version: str = "1.0.0"

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}

    def initialize(self) -> None:
        """Initialize service resources."""
        pass

    def shutdown(self) -> None:
        """Clean up service resources."""
        pass

    def health_check(self) -> dict[str, Any]:
        """Return service health status."""
        return {
            "service": self.service_name,
            "version": self.version,
            "status": "healthy",
        }


def validate_config(config: dict[str, Any]) -> bool:
    """Validate service configuration."""
    required_keys = ["service_name"]
    return all(key in config for key in required_keys)


def run_self_test() -> dict[str, Any]:
    """Run self-test to verify service is functional."""
    results = {
        "test_service_base": False,
        "test_health_check": False,
        "test_config_validation": False,
    }

    # Test ServiceBase instantiation
    try:
        service = ServiceBase(config={"service_name": "test"})
        results["test_service_base"] = True
    except Exception:
        pass

    # Test health check
    try:
        health = service.health_check()
        results["test_health_check"] = (
            health.get("status") == "healthy"
            and health.get("service") == "zo_sentinel"
        )
    except Exception:
        pass

    # Test config validation
    try:
        results["test_config_validation"] = validate_config(
            {"service_name": "test"}
        )
    except Exception:
        pass

    all_passed = all(results.values())
    results["overall"] = "PASS" if all_passed else "FAIL"
    return results


if __name__ == "__main__":
    result = run_self_test()
    if result["overall"] == "PASS":
        print("PASS")
    else:
        print(f"FAIL: {result}")
        sys.exit(1)