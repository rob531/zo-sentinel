# org_api_key_manager.py
"""
Pure utility module exposing the :class:`OrgAPIKeyManager`.

The manager reads ``org_id``, ``api_key`` and ``permissions`` from a
metadata dictionary, validates the ``org_id`` as a UUIDv4 and stores the
values.  No persistence, networking or external dependencies are used.
"""

import uuid
from typing import Any, Dict, List


class OrgAPIKeyManager:
    """
    Simple container for organization API‑key information.

    Parameters
    ----------
    metadata: dict
        Must contain the keys ``org_id``, ``api_key`` and ``permissions``.
        ``org_id`` must be a UUID version 4 string.
    """

    def __init__(self, metadata: Dict[str, Any]) -> None:
        # ---- basic key existence checks ------------------------------------
        required_keys = {"org_id", "api_key", "permissions"}
        missing = required_keys - metadata.keys()
        if missing:
            raise ValueError(f"Missing required metadata fields: {', '.join(missing)}")

        # ---- org_id validation (UUIDv4) ------------------------------------
        org_id_raw = metadata["org_id"]
        if not isinstance(org_id_raw, str):
            raise ValueError("org_id must be a string")
        try:
            parsed_uuid = uuid.UUID(org_id_raw, version=4)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"org_id '{org_id_raw}' is not a valid UUIDv4") from exc

        # The uuid.UUID constructor accepts any UUID; we must ensure it is version 4.
        if parsed_uuid.version != 4:
            raise ValueError(f"org_id '{org_id_raw}' is not a UUID version 4")

        # ---- api_key validation ---------------------------------------------
        api_key = metadata["api_key"]
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key must be a non‑empty string")

        # ---- permissions validation ------------------------------------------
        permissions = metadata["permissions"]
        if not isinstance(permissions, list):
            raise ValueError("permissions must be a list")
        if not all(isinstance(p, str) for p in permissions):
            raise ValueError("each permission must be a string")

        # ---- store validated values -------------------------------------------
        self.org_id: str = str(parsed_uuid)          # canonical string form
        self.api_key: str = api_key
        self.permissions: List[str] = permissions

    # -------------------------------------------------------------------------
    # Helper / representation methods (optional but handy for debugging)
    # -------------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(org_id={self.org_id!r}, "
            f"api_key={self.api_key!r}, permissions={self.permissions!r})"
        )

    # -------------------------------------------------------------------------
    # Example utility method – not required by the task but useful
    # -------------------------------------------------------------------------
    def has_permission(self, permission: str) -> bool:
        """Return ``True`` if *permission* is listed in the manager's permissions."""
        return permission in self.permissions


# -------------------------------------------------------------------------
# Self‑test executed when the module is run directly
# -------------------------------------------------------------------------
if __name__ == "__main__":
    # Test data – a valid UUIDv4 string
    test_metadata = {
        "org_id": "550e8400-e29b-41d4-a716-446655440000",
        "api_key": "test_key",
        "permissions": ["read", "write"],
    }

    # Create the manager; any exception will abort the script
    manager = OrgAPIKeyManager(test_metadata)

    # Assertions for the self‑test
    assert isinstance(manager.org_id, str), "org_id should be a string"
    # Validate that the stored org_id is a proper UUIDv4
    parsed = uuid.UUID(manager.org_id)
    assert parsed.version == 4, "org_id is not a UUIDv4"
    assert manager.api_key == "test_key", "api_key mismatch"
    assert manager.permissions == ["read", "write"], "permissions mismatch"

    print("PASS")