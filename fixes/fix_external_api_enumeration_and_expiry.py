#!/usr/bin/env python3
"""
fix_external_api_enumeration_and_expiry.py

Two hardening changes to sentinel_external_api.py before colleague handoff:

  A. Key expiry support. load_api_keys() learns to parse an optional
     'expires: <ISO-8601 timestamp>' comment that appears on the line
     IMMEDIATELY BEFORE a key. Keys whose expires timestamp has passed
     are silently skipped (treated as absent). No daemon needed; the
     next restart (or SIGHUP handler, future work) re-reads the file.

     File format examples (all accepted):
        # colleague: alice, no expiry
        longkeyhere123

        # colleague: bob, 36h trial
        # expires: 2026-04-20T01:30:00Z
        anotherkey456

  B. Reject pure-wildcard / near-empty search queries. This closes the
     easiest enumeration vector: an attacker with a stolen key can today
     walk the full 791-row registry in ~16 paginated calls via q='%'.
     After this patch, queries consisting only of SQL wildcards (%, _),
     whitespace, or fewer than 2 non-wildcard characters return 400.

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/sentinel_external_api.py")

# ---- Patch A: key expiry parsing ------------------------------------------

A_OLD = """def load_api_keys() -> set:
    global KEYS
    keys = set()
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, 'r') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#'):
                        keys.add(stripped)
        except Exception as e:
            if logger:
                logger.error(f'Failed to load API keys: {e}')
    KEYS = keys
    return keys"""

A_NEW = """def load_api_keys() -> set:
    '''Load API keys from file. Supports optional expiry via a comment line
    'expires: <ISO-8601>' IMMEDIATELY BEFORE a key. Expired keys are silently
    skipped (not loaded into KEYS).'''
    global KEYS
    keys = set()
    pending_expiry = None
    loaded = 0
    expired = 0
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, 'r') as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        pending_expiry = None
                        continue
                    if line.startswith('#'):
                        # look for 'expires: <iso>' (case-insensitive)
                        body = line.lstrip('#').strip()
                        if body.lower().startswith('expires:'):
                            ts = body.split(':', 1)[1].strip()
                            try:
                                # Accept 'Z' suffix and bare ISO formats
                                ts_clean = ts.replace('Z', '+00:00')
                                pending_expiry = datetime.fromisoformat(ts_clean)
                                if pending_expiry.tzinfo is None:
                                    pending_expiry = pending_expiry.replace(tzinfo=timezone.utc)
                            except Exception:
                                pending_expiry = None
                        continue
                    # Non-comment non-empty line: it's a key
                    if pending_expiry is not None:
                        if datetime.now(timezone.utc) >= pending_expiry:
                            expired += 1
                            pending_expiry = None
                            continue
                    keys.add(line)
                    loaded += 1
                    pending_expiry = None
        except Exception as e:
            if logger:
                logger.error(f'Failed to load API keys: {e}')
    if logger:
        logger.info(f'Keys loaded: {loaded} active, {expired} expired and skipped')
    KEYS = keys
    return keys"""

# Also need to ensure the `from datetime import ... timezone` import is present.
# The file currently has `from datetime import datetime` only.
IMPORT_OLD = "from datetime import datetime"
IMPORT_NEW = "from datetime import datetime, timezone"

# ---- Patch B: reject pure-wildcard search ---------------------------------

B_OLD = """    q: str = Query(..., min_length=1, max_length=200, description='Search query'),
    limit: int = Query(10, ge=1, le=50, description='Max results to return'),
    _api_key: str = Depends(verify_api_key)
):
    q = q.strip()
    like_pattern = f'%{q}%'"""

B_NEW = """    q: str = Query(..., min_length=1, max_length=200, description='Search query (minimum 2 non-wildcard chars)'),
    limit: int = Query(10, ge=1, le=50, description='Max results to return'),
    _api_key: str = Depends(verify_api_key)
):
    q = q.strip()
    # Anti-enumeration: reject queries that are pure wildcards or near-empty.
    # Without this a caller can walk the full registry via q='%' paginated calls.
    content_chars = q.replace('%', '').replace('_', '').strip()
    if len(content_chars) < 2:
        raise HTTPException(
            status_code=400,
            detail='Search query must contain at least 2 non-wildcard characters'
        )
    like_pattern = f'%{q}%'"""


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("sentinel_external_api: key expiry + anti-enumeration")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    # Import (needed by Patch A)
    if "from datetime import datetime, timezone" in src:
        print("  [skip imp] timezone already imported")
    elif IMPORT_OLD in src:
        src = src.replace(IMPORT_OLD, IMPORT_NEW, 1)
        print("  [patch imp] added timezone to datetime import")
        changed = True
    else:
        print("  [WARN imp] datetime import anchor not found; may already be custom")

    # Patch A
    if "pending_expiry = None" in src and "expired and skipped" in src:
        print("  [skip A] key expiry parsing already present")
    elif A_OLD in src:
        src = src.replace(A_OLD, A_NEW, 1)
        print("  [patch A] load_api_keys now honours 'expires:' comments")
        changed = True
    else:
        print("  [FAIL A] load_api_keys anchor not found verbatim")
        return 2

    # Patch B
    if "content_chars = q.replace('%'" in src:
        print("  [skip B] anti-enumeration check already present")
    elif B_OLD in src:
        src = src.replace(B_OLD, B_NEW, 1)
        print("  [patch B] /v1/search rejects pure-wildcard queries")
        changed = True
    else:
        print("  [FAIL B] /v1/search anchor not found verbatim")
        return 2

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
    print("\nNow update the keys file with an expiring key for your colleague,")
    print("then restart the daemon. Example:")
    print("")
    print("  cat > /home/workspace/zo_sentinel/config/external_api_keys.txt <<'EOF'")
    print("  # robin local test key -- no expiry")
    print("  test-key-robin-apr18")
    print("  ")
    print("  # colleague-name, 36h trial from 2026-04-18T17:30:00Z")
    print("  # expires: 2026-04-20T05:30:00Z")
    print("  <paste-new-token-here>")
    print("  EOF")
    print("")
    print("  chmod 600 /home/workspace/zo_sentinel/config/external_api_keys.txt")
    print("  pkill -9 -f 'sentinel_external_api.py'")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/sentinel_external_api.py \\")
    print("    >> /home/workspace/logs/sentinel_external_api.log 2>&1 &")
    print("  sleep 3")
    print("  # test pure-wildcard is rejected:")
    print("  curl -s -H 'X-API-Key: test-key-robin-apr18' \\")
    print("    'http://127.0.0.1:8791/v1/search?q=%25'")
    print("  # expect: {\"detail\":\"Search query must contain at least 2 non-wildcard characters\"}")
    return 0


if __name__ == "__main__":
    sys.exit(main())