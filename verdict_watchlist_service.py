"""
Server watchlist service for Tier‑1 app foundation.

Provides:
    - add_watch(org_id, user_id, server_id)
    - on_verdict_change(server_id, old_tier, new_tier)

When a watched server changes tier a notification is queued for each watcher.
Persistence is handled via a very small in‑process ``WriteService`` – no external
connectors are used.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any


# --------------------------------------------------------------------------- #
# Simple in‑process persistence layer (write_service)
# --------------------------------------------------------------------------- #
class WriteService:
    """
    Minimal key/value store used to persist the watchlist.
    In a real deployment this would be backed by a database or durable store.
    """
    def __init__(self):
        self._store: Dict[str, Any] = {}

    def write(self, key: str, value: Any) -> None:
        """Persist ``value`` under ``key``."""
        self._store[key] = value

    def read(self, key: str, default: Any = None) -> Any:
        """Retrieve the value for ``key``; return ``default`` if missing."""
        return self._store.get(key, default)


# --------------------------------------------------------------------------- #
# Notification queue – in‑memory for the purpose of this exercise
# --------------------------------------------------------------------------- #
_notification_queue: List[Dict[str, Any]] = []


def queue_notification(org_id: str, user_id: str, server_id: str,
                       old_tier: str, new_tier: str) -> None:
    """
    Append a notification dict to the in‑memory queue.
    """
    _notification_queue.append({
        "org_id": org_id,
        "user_id": user_id,
        "server_id": server_id,
        "old_tier": old_tier,
        "new_tier": new_tier,
    })


# --------------------------------------------------------------------------- #
# Watchlist service implementation
# --------------------------------------------------------------------------- #
# The persistence key used by WriteService
_WATCHLIST_KEY = "verdict_watchlist"


@dataclass(frozen=True)
class Watcher:
    """Simple value object representing a watch entry."""
    org_id: str
    user_id: str


class VerdictWatchlistService:
    """
    Core service handling watch registration and verdict change notifications.
    """
    def __init__(self, write_service: WriteService):
        self._write_service = write_service
        # Load existing watchlist or initialise an empty dict.
        # Structure: { server_id: [Watcher, ...], ... }
        self._watchlist: Dict[str, List[Watcher]] = self._write_service.read(
            _WATCHLIST_KEY, {}
        )

    # ------------------------------------------------------------------- #
    # Public API
    # ------------------------------------------------------------------- #
    def add_watch(self, org_id: str, user_id: str, server_id: str) -> None:
        """
        Register ``org_id``/``user_id`` to be notified when ``server_id`` changes tier.
        Idempotent – adding the same watch twice has no side‑effects.
        """
        watcher = Watcher(org_id, user_id)
        watchers = self._watchlist.setdefault(server_id, [])

        if watcher not in watchers:
            watchers.append(watcher)
            self._persist()
        # else: already present – nothing to do

    def on_verdict_change(self,
                          server_id: str,
                          old_tier: str,
                          new_tier: str) -> None:
        """
        Called when a server's tier (verdict) changes.
        If the tier actually changed and there are watchers for the server,
        queue a notification for each watcher.
        """
        if old_tier == new_tier:
            # No tier change – nothing to notify.
            return

        watchers = self._watchlist.get(server_id, [])
        for watcher in watchers:
            queue_notification(
                org_id=watcher.org_id,
                user_id=watcher.user_id,
                server_id=server_id,
                old_tier=old_tier,
                new_tier=new_tier,
            )

    # ------------------------------------------------------------------- #
    # Helper methods
    # ------------------------------------------------------------------- #
    def _persist(self) -> None:
        """Write the current watchlist to the WriteService."""
        # Convert dataclasses to plain dicts for easier serialization (if needed)
        serialisable = {
            server: [watcher.__dict__ for watcher in watchers]
            for server, watchers in self._watchlist.items()
        }
        self._write_service.write(_WATCHLIST_KEY, serialisable)

    # Expose the queue for testing / introspection (read‑only)
    @staticmethod
    def get_notification_queue() -> List[Dict[str, Any]]:
        """Return the current list of queued notifications."""
        return list(_notification_queue)


# --------------------------------------------------------------------------- #
# Simple sanity‑check when run as a script
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Initialise services
    ws = WriteService()
    svc = VerdictWatchlistService(ws)

    # Test data
    ORG = "org-123"
    USER = "user-456"
    SERVER = "server-789"
    OLD_TIER = "bronze"
    NEW_TIER = "silver"

    # 1. Register a watch
    svc.add_watch(ORG, USER, SERVER)

    # 2. Simulate a tier change
    svc.on_verdict_change(SERVER, OLD_TIER, NEW_TIER)

    # 3. Verify a notification was queued
    queued = VerdictWatchlistService.get_notification_queue()
    assert len(queued) == 1, f"Expected 1 notification, got {len(queued)}"
    notif = queued[0]
    assert notif["org_id"] == ORG
    assert notif["user_id"] == USER
    assert notif["server_id"] == SERVER
    assert notif["old_tier"] == OLD_TIER
    assert notif["new_tier"] == NEW_TIER

    print("PASS")