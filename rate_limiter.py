import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimiter:
    """
    Token bucket rate limiter with minute/hour granularity.
    Thread-safe for concurrent FastAPI requests.
    No external dependencies - uses in-memory dict with timestamp buckets.
    """
    
    def __init__(self, requests_per_minute: int = 60, requests_per_hour: int = 500):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        
        # {client_ip: [timestamp, ...]}
        self._minute_buckets: Dict[str, List[float]] = {}
        self._hour_buckets: Dict[str, List[float]] = {}
        
        self._lock = threading.Lock()
        self._cleanup_interval = 300  # seconds
        self._last_cleanup = time.time()
    
    def _cleanup_old_entries(self) -> None:
        """Remove timestamps older than 1 hour to prevent memory leak."""
        current_time = time.time()
        cutoff = current_time - 3600  # 1 hour ago
        
        if self._last_cleanup + self._cleanup_interval > current_time:
            return
        
        with self._lock:
            for ip in list(self._minute_buckets.keys()):
                self._minute_buckets[ip] = [ts for ts in self._minute_buckets[ip] if ts > cutoff]
                if not self._minute_buckets[ip]:
                    del self._minute_buckets[ip]
            
            for ip in list(self._hour_buckets.keys()):
                self._hour_buckets[ip] = [ts for ts in self._hour_buckets[ip] if ts > cutoff]
                if not self._hour_buckets[ip]:
                    del self._hour_buckets[ip]
            
            self._last_cleanup = current_time
    
    def check(self, client_ip: str) -> bool:
        """
        Check if request is allowed for client IP.
        
        Args:
            client_ip: Client identifier (IP address)
            
        Returns:
            True if request is within rate limits
            
        Raises:
            HTTPException(429) if rate limit exceeded
        """
        self._cleanup_old_entries()
        
        current_time = time.time()
        minute_cutoff = current_time - 60  # 1 minute ago
        hour_cutoff = current_time - 3600  # 1 hour ago
        
        with self._lock:
            if client_ip not in self._minute_buckets:
                self._minute_buckets[client_ip] = []
            
            minute_requests = [ts for ts in self._minute_buckets[client_ip] if ts > minute_cutoff]
            
            if len(minute_requests) >= self.requests_per_minute:
                retry_after = int(60 - (current_time - min(minute_requests)))
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {self.requests_per_minute} requests per minute",
                    headers={"Retry-After": str(max(1, retry_after))}
                )
            
            if client_ip not in self._hour_buckets:
                self._hour_buckets[client_ip] = []
            
            hour_requests = [ts for ts in self._hour_buckets[client_ip] if ts > hour_cutoff]
            
            if len(hour_requests) >= self.requests_per_hour:
                retry_after = int(3600 - (current_time - min(hour_requests)))
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {self.requests_per_hour} requests per hour",
                    headers={"Retry-After": str(max(1, retry_after))}
                )
            
            self._minute_buckets[client_ip] = minute_requests + [current_time]
            self._hour_buckets[client_ip] = hour_requests + [current_time]
        
        return True
    
    def get_status(self, client_ip: str) -> dict:
        """Get current rate limit status for a client IP."""
        current_time = time.time()
        minute_cutoff = current_time - 60
        hour_cutoff = current_time - 3600
        
        with self._lock:
            minute_requests = [ts for ts in self._minute_buckets.get(client_ip, []) if ts > minute_cutoff]
            hour_requests = [ts for ts in self._hour_buckets.get(client_ip, []) if ts > hour_cutoff]
        
        return {
            "minute_remaining": max(0, self.requests_per_minute - len(minute_requests)),
            "hour_remaining": max(0, self.requests_per_hour - len(hour_requests)),
            "minute_reset_in": 60 - (current_time - min(minute_requests)) if minute_requests else 0,
            "hour_reset_in": 3600 - (current_time - min(hour_requests)) if hour_requests else 0
        }
    
    def reset(self, client_ip: Optional[str] = None) -> None:
        """Reset rate limit counters for a client IP or all clients."""
        with self._lock:
            if client_ip:
                self._minute_buckets.pop(client_ip, None)
                self._hour_buckets.pop(client_ip, None)
            else:
                self._minute_buckets.clear()
                self._hour_buckets.clear()


def rate_limit_dependency(
    requests_per_minute: int = 60,
    requests_per_hour: int = 500
):
    """
    Factory function for FastAPI Depends() injection.
    
    Usage:
        @app.post("/endpoint")
        async def my_endpoint(limiter: RateLimiter = Depends(rate_limit_dependency())):
            ...
    """
    limiter = RateLimiter(requests_per_minute, requests_per_hour)
    
    async def _rate_limit(request: Request) -> bool:
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        return limiter.check(client_ip)
    
    return Depends(_rate_limit)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for global rate limiting.
    
    Usage:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=60, requests_per_hour=500)
    """
    
    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        requests_per_hour: int = 500
    ):
        super().__init__(app)
        self.limiter = RateLimiter(requests_per_minute, requests_per_hour)
    
    async def dispatch(self, request: Request, call_next):
        client_ip = self._extract_client_ip(request)
        
        try:
            self.limiter.check(client_ip)
        except HTTPException:
            raise
        
        response = await call_next(request)
        return response
    
    def _extract_client_ip(self, request: Request) -> str:
        """Extract client IP from request headers."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        
        if request.client:
            return request.client.host
        
        return "unknown"


def create_default_limiter() -> RateLimiter:
    """Create a default rate limiter instance with standard limits."""
    return RateLimiter(requests_per_minute=60, requests_per_hour=500)


# Global default limiter instance for convenience
_default_limiter: Optional[RateLimiter] = None


def get_default_limiter() -> RateLimiter:
    """Get or create the default global rate limiter."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = create_default_limiter()
    return _default_limiter


def rate_limit(
    requests_per_minute: int = 60,
    requests_per_hour: int = 500
) -> Depends:
    """
    Convenience decorator for rate limiting endpoints.
    
    Usage:
        @app.post("/api/resource")
        @rate_limit(requests_per_minute=100)
        async def my_endpoint(request: Request):
            ...
    """
    limiter = RateLimiter(requests_per_minute, requests_per_hour)
    
    async def _limit(request: Request):
        client_ip = _get_client_ip(request)
        return limiter.check(client_ip)
    
    return Depends(_limit)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    return request.client.host if request.client else "unknown"