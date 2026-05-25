#!/usr/bin/env python3
"""
fix_external_api_rate_limit_enforcement.py

Wire up rate limiting on sentinel_external_api.py.

Root cause: check_rate_limit() is defined but never invoked by any
endpoint. A placeholder _rate_ok: bool = Depends(lambda key: True) on
/v1/mcp/{server_id} does nothing. The other three authenticated
endpoints don't even have the placeholder.

This patch:
  A. Adds a proper enforce_rate_limit FastAPI dependency that depends on
     verify_api_key, then consults the module-level rate_limiters dict.
     Returns 429 with a Retry-After header when the caller exceeds
     RATE_LIMIT (60) requests in RATE_WINDOW (60 seconds).
  B. Replaces the no-op _rate_ok dependency on /v1/mcp/{server_id} with
     _rate_ok: str = Depends(enforce_rate_limit).
  C. Adds _rate_ok: str = Depends(enforce_rate_limit) to the three other
     authenticated endpoints (/v1/search, /threats, /risk). /v1/health
     stays unlimited (standard practice for liveness probes).

Notes:
  - Limit is per API key, not per IP. Rotating IPs won't escape it;
    a legitimate user behind a NAT won't be falsely throttled.
  - In-process limiter. Single-worker setup (confirmed: uvicorn.run has
    no workers= arg) makes this correct. If you ever add --workers N,
    each worker gets its own counter -> real limit = N * RATE_LIMIT.
    Redis-backed limiter is a future concern.
  - Rate limit does NOT apply to 401/403 responses (wrong/missing key).
    That's by design: verify_api_key raises HTTPException before the
    rate-limit dependency runs, and FastAPI short-circuits on raise.

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/sentinel_external_api.py")

# ---- Patch A: replace bare check_rate_limit() with a proper dependency ----

A_OLD = """def check_rate_limit(client_id: str) -> tuple:
    now = time.time()
    window_start = now - RATE_WINDOW
    limiter = rate_limiters[client_id]
    while limiter and limiter[0] < window_start:
        limiter.popleft()
    if len(limiter) >= RATE_LIMIT:
        oldest = limiter[0]
        retry_after = int(oldest - window_start) + 1
        return False, retry_after
    limiter.append(now)
    return True, 0"""

A_NEW = """def check_rate_limit(client_id: str) -> tuple:
    '''Sliding-window counter. Returns (ok: bool, retry_after: int).
    Called by enforce_rate_limit dependency below -- do not call directly
    from endpoints.'''
    now = time.time()
    window_start = now - RATE_WINDOW
    limiter = rate_limiters[client_id]
    while limiter and limiter[0] < window_start:
        limiter.popleft()
    if len(limiter) >= RATE_LIMIT:
        oldest = limiter[0]
        retry_after = int(oldest - window_start) + 1
        return False, retry_after
    limiter.append(now)
    return True, 0


def enforce_rate_limit(api_key: str = Depends(verify_api_key)) -> str:
    '''FastAPI dependency that enforces the per-key rate limit.
    Raises 429 with Retry-After header if the caller exceeds RATE_LIMIT
    requests in RATE_WINDOW seconds. Must be Depends()-ed AFTER
    verify_api_key so that invalid keys never count against a limit.
    Returns the api_key so downstream deps can use it if desired.'''
    ok, retry_after = check_rate_limit(api_key)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f'Rate limit exceeded: {RATE_LIMIT} requests per {RATE_WINDOW}s per key',
            headers={'Retry-After': str(retry_after)},
        )
    return api_key"""

# ---- Patch B: fix the no-op placeholder on /v1/mcp/{server_id} ------------

B_OLD = """async def get_mcp_assessment(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _api_key: str = Depends(verify_api_key),
    _rate_ok: bool = Depends(lambda key: True)
):"""

B_NEW = """async def get_mcp_assessment(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _rate_ok: str = Depends(enforce_rate_limit),
):"""

# ---- Patch C: add rate-limit dep to /v1/search ----------------------------

C_OLD = """async def search_mcp(
    q: str = Query(..., min_length=1, max_length=200, description='Search query (minimum 2 non-wildcard chars)'),
    limit: int = Query(10, ge=1, le=50, description='Max results to return'),
    _api_key: str = Depends(verify_api_key)
):"""

C_NEW = """async def search_mcp(
    q: str = Query(..., min_length=1, max_length=200, description='Search query (minimum 2 non-wildcard chars)'),
    limit: int = Query(10, ge=1, le=50, description='Max results to return'),
    _rate_ok: str = Depends(enforce_rate_limit),
):"""

# ---- Patch D: add rate-limit dep to /v1/mcp/{server_id}/threats -----------

D_OLD = """async def get_mcp_threats(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    limit: int = Query(20, ge=1, le=100, description='Max threats to return'),
    _api_key: str = Depends(verify_api_key)
):"""

D_NEW = """async def get_mcp_threats(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    limit: int = Query(20, ge=1, le=100, description='Max threats to return'),
    _rate_ok: str = Depends(enforce_rate_limit),
):"""

# ---- Patch E: add rate-limit dep to /v1/mcp/{server_id}/risk --------------

E_OLD = """async def get_mcp_risk(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _api_key: str = Depends(verify_api_key)
):"""

E_NEW = """async def get_mcp_risk(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _rate_ok: str = Depends(enforce_rate_limit),
):"""


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("sentinel_external_api: wire up rate limit enforcement")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False
    patches = [
        ("A", "rate-limit dep+func", A_OLD, A_NEW, "def enforce_rate_limit"),
        ("B", "/v1/mcp/{id}",         B_OLD, B_NEW, None),
        ("C", "/v1/search",           C_OLD, C_NEW, None),
        ("D", "/v1/mcp/{id}/threats", D_OLD, D_NEW, None),
        ("E", "/v1/mcp/{id}/risk",    E_OLD, E_NEW, None),
    ]

    # Global idempotency: if enforce_rate_limit is defined AND all four
    # endpoints already depend on it, everything is already applied.
    endpoint_count = src.count("Depends(enforce_rate_limit)")
    already_defined = "def enforce_rate_limit(" in src
    if already_defined and endpoint_count >= 4:
        print("  [skip all] enforce_rate_limit defined and wired on 4+ endpoints")
        print("  [noop] all patches already applied")
        return 0

    for label, desc, old, new, marker in patches:
        # Per-patch idempotency: marker presence OR new-already-in-file
        if marker and marker in src:
            print(f"  [skip {label}] {desc}: already present")
            continue
        if new in src:
            print(f"  [skip {label}] {desc}: already patched")
            continue
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}: applied")
        changed = True

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nRestart:")
    print("  pkill -9 -f 'sentinel_external_api.py'")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/sentinel_external_api.py \\")
    print("    >> /home/workspace/logs/sentinel_external_api.log 2>&1 &")
    print("  sleep 3")
    print("")
    print("Verify rate limit fires (66 rapid requests, last 10 should be 429):")
    print("  KEY='test-key-robin-apr18'")
    print("  for i in $(seq 1 66); do")
    print("    code=$(curl -s -o /dev/null -w '%{http_code}' -H \"X-API-Key: $KEY\" \\")
    print("      'http://127.0.0.1:8791/v1/search?q=github&limit=1')")
    print("    echo \"$i: $code\"")
    print("  done | tail -12")
    print("  # expect: first 60 show '200', remaining show '429'")
    return 0


if __name__ == "__main__":
    sys.exit(main())