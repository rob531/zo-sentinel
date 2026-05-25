#!/usr/bin/env python3
"""
patch_fetcher_url_routing.py

Fix: ecosystems_metadata_fetcher only works for github URLs. npm URLs
(70% of our registry) return 0 cousins because we pass the npm URL as
repository_url, which ecosyste.ms interprets literally -- finding
packages whose repository field is npmjs.com/... (essentially zero).

The fix: detect URL type and use the appropriate lookup.
  - github URL  -> /packages/lookup?repository_url=<url>  (current behavior)
  - npm URL     -> /packages/lookup?ecosystem=npm&name=<parsed_pkg_name>
  - pypi URL    -> /packages/lookup?ecosystem=pypi&name=<parsed_pkg_name>
  - other       -> try repository_url as last resort

Extracting npm package names:
  https://www.npmjs.com/package/foo           -> foo
  https://www.npmjs.com/package/@scope/bar    -> @scope/bar
  https://npmjs.com/package/foo               -> foo

After this patch, a FULL re-fetch is needed to re-cache the 50 entries
we already have (most of which are 'ok' with 0 cousins because of this
bug). The patcher also truncates mcp_ecosystems_metadata so the next
fetcher cycle picks up everything fresh.

Idempotent via marker check.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

TARGET = Path("/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py")
MARKER = "def _classify_url"
EXECUTE_URL = "http://127.0.0.1:8772/execute"

# ---- Inject classifier + dual-lookup support ------------------------

# Anchor 1: replace fetch_ecosystems_for_url() to dispatch on URL type
OLD_FETCH = '''def fetch_ecosystems_for_url(github_url: str) -> dict:
    """Return dict with lookup_status, packages (list), raw_bytes.
    Never raises. Handles 404, 429, 5xx, timeouts, parse errors."""
    try:
        r = requests.get(
            ECOSYSTEMS_ENDPOINT,
            params={"repository_url": github_url},
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "From": FROM_HEADER,
            },
            timeout=API_TIMEOUT_S,
        )
    except requests.exceptions.Timeout:
        return {"lookup_status": "timeout", "packages": [], "raw_bytes": 0,
                "error": "timeout"}
    except Exception as e:
        return {"lookup_status": "error", "packages": [], "raw_bytes": 0,
                "error": f"{type(e).__name__}: {str(e)[:120]}"}'''

NEW_FETCH = '''def _classify_url(url: str) -> tuple[str, dict]:
    """Return (lookup_mode, query_params) for the correct ecosyste.ms call.

    lookup_mode is one of:
      - 'repo'    -> pass url as repository_url (github, gitlab, bitbucket)
      - 'package' -> pass ecosystem + name (npm, pypi direct registry URLs)
      - 'unknown' -> try repo lookup as best-effort fallback
    """
    if not url:
        return "unknown", {}
    low = url.lower()

    # github/gitlab/bitbucket -> repository lookup
    if any(h in low for h in ("github.com", "gitlab.com", "bitbucket.org",
                              "codeberg.org", "sr.ht")):
        return "repo", {"repository_url": url}

    # npm package URL
    if "npmjs.com/package/" in low or "npmjs.org/package/" in low:
        # Extract everything after /package/, including scoped names like @foo/bar
        idx = low.find("/package/")
        name_part = url[idx + len("/package/"):].strip("/").split("?")[0].split("#")[0]
        if name_part:
            return "package", {"ecosystem": "npm", "name": name_part}

    # pypi package URL
    if "pypi.org/project/" in low or "pypi.python.org/pypi/" in low:
        for marker in ("/project/", "/pypi/"):
            if marker in low:
                idx = low.find(marker)
                name_part = url[idx + len(marker):].strip("/").split("/")[0].split("?")[0]
                if name_part:
                    return "package", {"ecosystem": "pypi", "name": name_part}

    # Fallback: pretend it's a repo URL and hope
    return "unknown", {"repository_url": url}


def _flatten_package_response(payload) -> list:
    """ecosyste.ms /packages/lookup returns either a list (for repo lookup)
    or a single dict (for ecosystem+name lookup). Normalize."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "results" in payload:
            return payload["results"]
        if "packages" in payload:
            return payload["packages"]
        # Single package dict -- wrap
        if "ecosystem" in payload or "name" in payload:
            return [payload]
    return []


def fetch_ecosystems_for_url(github_url: str) -> dict:
    """Return dict with lookup_status, packages (list), raw_bytes.
    Never raises. Handles 404, 429, 5xx, timeouts, parse errors.

    Commit B-adjacent fix: dispatches on URL type (github/npm/pypi) so we
    get cross-registry data for npm-sourced MCPs too (was 0% coverage).
    """
    mode, params = _classify_url(github_url)
    try:
        r = requests.get(
            ECOSYSTEMS_ENDPOINT,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "From": FROM_HEADER,
            },
            timeout=API_TIMEOUT_S,
        )
    except requests.exceptions.Timeout:
        return {"lookup_status": "timeout", "packages": [], "raw_bytes": 0,
                "error": f"timeout (mode={mode})"}
    except Exception as e:
        return {"lookup_status": "error", "packages": [], "raw_bytes": 0,
                "error": f"{type(e).__name__} (mode={mode}): {str(e)[:120]}"}'''

# Anchor 2: the downstream parsing still uses `packages = payload` etc.
# Replace that block too so package-mode single-dict responses work.
OLD_PARSE = '''    try:
        payload = r.json()
    except Exception as e:
        return {"lookup_status": "parse_error", "packages": [],
                "raw_bytes": raw_bytes,
                "error": f"json decode: {e}"}

    if isinstance(payload, list):
        packages = payload
    elif isinstance(payload, dict):
        packages = payload.get("results") or payload.get("packages") or []
    else:
        packages = []

    return {"lookup_status": "ok", "packages": packages,
            "raw_bytes": raw_bytes, "error": None}'''

NEW_PARSE = '''    try:
        payload = r.json()
    except Exception as e:
        return {"lookup_status": "parse_error", "packages": [],
                "raw_bytes": raw_bytes,
                "error": f"json decode: {e}"}

    packages = _flatten_package_response(payload)

    return {"lookup_status": "ok", "packages": packages,
            "raw_bytes": raw_bytes, "error": None}'''


def _backup(path: Path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def _truncate_stale_cache() -> bool:
    """Clear mcp_ecosystems_metadata so next fetcher cycle re-queries
    everything with the corrected URL routing. The 50 rows we have are
    mostly misleading (cousin_count=0 for npm entries that actually have
    dozens of cousins)."""
    try:
        r = requests.post(
            EXECUTE_URL,
            json={"sql": "DELETE FROM mcp_ecosystems_metadata",
                  "agent_id": "patch_fetcher_url_routing", "wait": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [WARN] truncate failed: {e}")
        return False


def main():
    print("=" * 60)
    print("patch_fetcher_url_routing.py")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if MARKER in src:
        print("  [skip] URL routing already applied")
        return 0

    if OLD_FETCH not in src:
        print("  [FAIL] fetch_ecosystems_for_url anchor not found")
        return 2
    if OLD_PARSE not in src:
        print("  [FAIL] payload-parse anchor not found")
        return 2

    src = src.replace(OLD_FETCH, NEW_FETCH, 1)
    print("  [patch A] _classify_url + _flatten_package_response added")
    src = src.replace(OLD_PARSE, NEW_PARSE, 1)
    print("  [patch B] response parsing uses _flatten_package_response")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"  [done] {TARGET.name} patched")

    print("\n  [cleanup] truncating stale mcp_ecosystems_metadata (50 rows with bad routing)")
    if _truncate_stale_cache():
        print("  [cleanup] done; next fetcher cycle will re-query all stale servers")
    else:
        print("  [cleanup] WARN: truncate failed; expect duplicate-ish data on refetch")

    print("\nRestart fetcher:")
    print("  pkill -f 'daemon_wrapper.sh ecosystems_metadata_fetcher'")
    print("  sleep 2")
    print("  source /home/workspace/zo_mesh/.zo_env")
    print("  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \\")
    print("    ecosystems_metadata_fetcher \\")
    print("    /home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py \\")
    print("    >> /home/workspace/logs/ecosystems_metadata_fetcher.log 2>&1 &")
    print("\nVerify npm URLs now yield cousins:")
    print("  sleep 90")
    print("  curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\")
    print("    -d '{\"sql\":\"SELECT COUNT(*) AS ok, SUM(CASE WHEN cousin_count > 0 THEN 1 ELSE 0 END) AS with_cousins FROM mcp_ecosystems_metadata WHERE lookup_status = \\'ok\\'\"}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())