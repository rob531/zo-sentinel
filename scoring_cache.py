"""
Scoring Cache Module

In-memory scoring cache with TTL for reducing repeated ws_query calls.
Thread-safe implementation with automatic expired entry cleanup.
"""

import threading
import time
from typing import Any, Optional


class ScoringCache:
    """Thread-safe in-memory cache for server scoring data with TTL."""
    
    def __init__(self):
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def get(self, server_id: str) -> Optional[dict]:
        """Retrieve cached scoring data for server_id.
        
        Automatically clears expired entries on each call to prevent unbounded growth.
        
        Args:
            server_id: Unique identifier for the MCP server
            
        Returns:
            Cached data dict if found and not expired, None otherwise
        """
        with self._lock:
            self._clear_expired_locked()
            
            entry = self._cache.get(server_id)
            if entry is None:
                return None
            
            if entry['expires_at'] <= time.time():
                del self._cache[server_id]
                return None
            
            return entry['data']
    
    def set(self, server_id: str, data: dict, ttl_seconds: int = 3600) -> None:
        """Store scoring data with TTL.
        
        Args:
            server_id: Unique identifier for the MCP server
            data: Scoring data dictionary to cache
            ttl_seconds: Time-to-live in seconds (default: 3600)
        """
        with self._lock:
            self._cache[server_id] = {
                'data': data,
                'expires_at': time.time() + ttl_seconds
            }
    
    def invalidate(self, server_id: str) -> None:
        """Remove cached entry for server_id.
        
        Args:
            server_id: Unique identifier for the MCP server
        """
        with self._lock:
            self._cache.pop(server_id, None)
    
    def clear_expired(self) -> int:
        """Remove all expired entries from cache.
        
        Returns:
            Number of entries removed
        """
        with self._lock:
            return self._clear_expired_locked()
    
    def _clear_expired_locked(self) -> int:
        """Clear expired entries. Must be called with lock held."""
        now = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry['expires_at'] <= now
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(expired_keys)
    
    def clear(self) -> None:
        """Clear all entries from cache."""
        with self._lock:
            self._cache.clear()


# Global cache instance - reduces repeated ws_query calls when signal_analyser
# processes same server multiple times per cycle
_cache = ScoringCache()