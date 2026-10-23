"""zo_sentinel: Sentinel service package for ZoGraph."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, TypeVar

# Package metadata
__version__ = "1.0.0"
__package_name__ = "zo_sentinel"

# Service configuration
SERVICE_NAME = os.environ.get("SERVICE_NAME", "zo-sentinel")
SERVICE_VERSION = os.environ.get("SERVICE_VERSION", __version__)

# Common type aliases
T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

# Status constants for sentinel operations
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_STAGED = "staged"

# Gate severity levels
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# Default thresholds
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RETRIES = 3


def get_service_info() -> dict[str, str]:
    """Return service identity information."""
    return {
        "name": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "package": __package_name__,
    }


def validate_status(status: str) -> bool:
    """Validate a status string against known values."""
    return status in {STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED, STATUS_FAILED, STATUS_STAGED}


def validate_severity(severity: str) -> bool:
    """Validate a severity string against known values."""
    return severity in {SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW}


# Re-export commonly used items for convenience
__all__ = [
    # Metadata
    "__version__",
    "__package_name__",
    "SERVICE_NAME",
    "SERVICE_VERSION",
    # Types
    "T",
    "F",
    # Status constants
    "STATUS_PENDING",
    "STATUS_ACTIVE",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_STAGED",
    # Severity constants
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "SEVERITY_LOW",
    # Default thresholds
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    # Functions
    "get_service_info",
    "validate_status",
    "validate_severity",
]


if __name__ == "__main__":
    # Self-test for package integrity
    import json
    
    print("Running zo_sentinel package self-test...")
    
    # Test 1: Package metadata
    assert __version__ == "1.0.0", f"Version mismatch: {__version__}"
    assert __package_name__ == "zo_sentinel", f"Package name mismatch: {__package_name__}"
    print("  [PASS] Package metadata")
    
    # Test 2: Service info
    info = get_service_info()
    assert info["name"] == SERVICE_NAME
    assert info["version"] == SERVICE_VERSION
    assert info["package"] == __package_name__
    print("  [PASS] Service info")
    
    # Test 3: Status validation
    assert validate_status(STATUS_ACTIVE) is True
    assert validate_status("invalid") is False
    print("  [PASS] Status validation")
    
    # Test 4: Severity validation
    assert validate_severity(SEVERITY_CRITICAL) is True
    assert validate_severity("invalid") is False
    print("  [PASS] Severity validation")
    
    # Test 5: __all__ exports
    required_exports = [
        "STATUS_PENDING",
        "STATUS_ACTIVE",
        "STATUS_COMPLETED",
        "STATUS_FAILED",
        "STATUS_STAGED",
        "SEVERITY_CRITICAL",
        "SEVERITY_HIGH",
        "SEVERITY_MEDIUM",
        "SEVERITY_LOW",
        "DEFAULT_TIMEOUT_SECONDS",
        "DEFAULT_MAX_RETRIES",
        "get_service_info",
        "validate_status",
        "validate_severity",
    ]
    for name in required_exports:
        assert name in __all__, f"Missing export: {name}"
    print("  [PASS] All required exports present")
    
    print("\nPASS")