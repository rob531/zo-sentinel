#!/usr/bin/env python3
"""
discovery_mcp_registry_paginator.py

Long-running daemon that paginates the OFFICIAL Model Context Protocol
Registry (the catalog the chairman pointed at via github.com/mcp) and writes
discovered servers directly into mcp_server_registry with
registry_source='mcp_registry'.

The official registry is a curated, authoritative catalog that exposes full
per-server metadata (canonical name, description, repository URL, packages,
remotes, version) in a single paginated REST endpoint, so -- unlike the npm
feed -- there is no separate candidate->promote fetch step needed. This mirrors
the candidate_smithery_promoter.py pattern (fetch external API + write straight
to mcp_server_registry) combined with the discovery_npm_paginator.py cursor
pattern (durable cursor for idempotent, incremental, resumable re-runs).

API (verified 2026-06-24):
  Base:        https://registry.modelcontextprotocol.io
  List:        GET /v0/servers?limit=<=100[&cursor=<opaque>][&updated_since=<RFC3339>]
  Pagination:  cursor-based; response.metadata.nextCursor is the opaque token
               for the next page (absent/empty => end of catalog).
  Incremental: updated_since=<RFC3339> returns only servers changed after a
               timestamp -> we persist last successful run time and pass it so
               steady-state cycles are cheap.
  Auth:        none (public, read-only).
  Rate limits: undocumented/generous; we self-throttle (FETCH_DELAY_MS) and
               back off on errors regardless.
  Supply:      ~9.6k latest server records (~29k server/version records) as of
               2026-05 -- high-value net-new authoritative supply, currently
               ABSENT from our feeds (we only have npm/smithery/github-search).

Each registry entry is normalized into one mcp_server_registry row keyed by a
stable server_id = md5("mcp_registry|<canonical_name>"). Canonical names in the
registry are reverse-DNS namespaced (e.g. "io.github.owner/repo") and unique,
so they are a stable dedup key. We DEDUP against existing rows before writing so
a server already discovered via npm/github/smithery is never duplicated by this
source (we check server_id presence; we never overwrite another source's row).
"""
import hashlib
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, '/home/workspace')

SERVICE_NAME = "discovery_mcp_registry_paginator"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

STATE_DIR = Path("/home/workspace/zo_sentinel/state")
STATE_FILE = STATE_DIR / "mcp_registry_pagination_cursor.json"
LOCK_FILE = Path("/home/workspace/logs/discovery_mcp_registry_paginator.lock")
LOG_FILE = Path("/home/workspace/logs/discovery_mcp_registry_paginator.log")

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
SERVERS_PATH = "/v0/servers"
PAGE_SIZE = 100

POLL_SECS = 1800
FETCH_TIMEOUT_SECS = 30
FETCH_DELAY_MS = 250          # polite throttle between page fetches
MAX_PAGES_PER_CYCLE = 50      # bound work per cycle; cursor resumes next cycle
HEARTBEAT_INTERVAL_SECS = 30
MAX_RETRIES = 4
BACKOFF_BASE_SECS = 2
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
REGISTRY_SOURCE = "mcp_registry"

stop_event = threading.Event()
heartbeat_thread = None


# --------------------------------------------------------------------------- #
# logging / pid / signals
# --------------------------------------------------------------------------- #
def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_pid_file(path):
    try:
        if path.exists():
            with open(path, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return None


def write_pid_file(path, pid):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(str(pid))
    except Exception as e:
        log(f"warn: failed to write PID file: {e}")


def remove_pid_file(path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def check_single_instance():
    own_pid = os.getpid()
    existing_pid = load_pid_file(LOCK_FILE)
    if existing_pid and existing_pid != own_pid:
        try:
            os.kill(existing_pid, 0)
            log(f"error: another instance already running as PID {existing_pid}")
            sys.exit(1)
        except OSError:
            log(f"info: stale lockfile detected, PID {existing_pid} not running, reclaiming")
    write_pid_file(LOCK_FILE, own_pid)


def signal_handler(signum, frame):
    sig_name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(signum, str(signum))
    log(f"info: received {sig_name}, shutting down gracefully")
    stop_event.set()


def setup_signals():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


# --------------------------------------------------------------------------- #
# WriteService helpers
# --------------------------------------------------------------------------- #
def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: ws_write failed for {table}: {e}")
        return None


def ws_query(sql):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: ws_query failed: {e}")
        return {"rows": [], "count": 0}


def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: ws_execute failed: {e}")
        return None


def send_heartbeat():
    payload = {
        "table": "service_health",
        "rows": {"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()},
        "wait": True,
    }
    try:
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        log(f"warn: heartbeat failed: {e}")


def heartbeat_loop():
    while not stop_event.is_set():
        stop_event.wait(timeout=HEARTBEAT_INTERVAL_SECS)
        if not stop_event.is_set():
            send_heartbeat()


def start_heartbeat():
    global heartbeat_thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()


# --------------------------------------------------------------------------- #
# cursor / incremental state
# --------------------------------------------------------------------------- #
def load_state():
    default = {
        "cursor": None,            # in-flight page cursor within a full sweep
        "last_full_sweep_at": None,  # RFC3339 of last completed full sweep
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
    except Exception as e:
        log(f"warn: failed to load state: {e}")
    return default


def save_state(state):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"warn: failed to save state: {e}")


# --------------------------------------------------------------------------- #
# fetch + normalize (pure functions -- unit-testable, no network/db)
# --------------------------------------------------------------------------- #
def fetch_page(cursor=None, updated_since=None):
    """Fetch one page of the registry with retry/backoff. Returns parsed dict or None."""
    params = {"limit": PAGE_SIZE}
    if cursor:
        params["cursor"] = cursor
    if updated_since:
        params["updated_since"] = updated_since
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    url = REGISTRY_BASE + SERVERS_PATH
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=FETCH_TIMEOUT_SECS)
            if resp.status_code == 429:
                wait = BACKOFF_BASE_SECS * (2 ** attempt)
                log(f"warn: 429 rate-limited, backing off {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = BACKOFF_BASE_SECS * (2 ** attempt)
            log(f"warn: fetch attempt {attempt + 1}/{MAX_RETRIES} failed: {e}; retry in {wait}s")
            time.sleep(wait)
    log("error: page fetch exhausted retries")
    return None


def extract_next_cursor(page):
    """Pull the opaque next-page cursor; empty/absent means end of catalog."""
    if not isinstance(page, dict):
        return None
    meta = page.get("metadata") or {}
    cur = meta.get("nextCursor") or meta.get("next_cursor")
    return cur or None


def compute_server_id(canonical_name):
    """Stable id. Mirrors the md5('<source>|<key>') scheme used by the npm/smithery
    promoters. Registry canonical names are reverse-DNS unique, so they dedup cleanly."""
    return hashlib.md5(f"mcp_registry|{canonical_name}".encode("utf-8")).hexdigest()


def _server_block(entry):
    """The list endpoint wraps each item as {'server': {...}, '_meta': {...}}.
    Tolerate either the wrapped form or a bare server object."""
    if not isinstance(entry, dict):
        return None, {}
    if "server" in entry and isinstance(entry["server"], dict):
        return entry["server"], (entry.get("_meta") or {})
    return entry, (entry.get("_meta") or {})


def normalize_entry(entry):
    """Convert one registry list entry into a mcp_server_registry row dict, or None
    if it lacks a usable canonical name. Pure: no network, no db."""
    server, meta = _server_block(entry)
    if not server:
        return None
    name = server.get("name") or ""
    if not name:
        return None

    description = (server.get("description") or "")[:1000]
    version = server.get("version") or ""

    repo = server.get("repository") or {}
    repo_url = ""
    if isinstance(repo, dict):
        repo_url = repo.get("url") or ""
    elif isinstance(repo, str):
        repo_url = repo

    # url precedence: repository -> first remote endpoint -> registry web page
    url = repo_url
    if not url:
        remotes = server.get("remotes") or []
        if isinstance(remotes, list) and remotes and isinstance(remotes[0], dict):
            url = remotes[0].get("url") or ""
    if not url:
        url = f"{REGISTRY_BASE}/v0/servers?search={name}"

    # packages: list of {registryType/registry_name, identifier, version, ...}
    packages = []
    for pkg in (server.get("packages") or []):
        if not isinstance(pkg, dict):
            continue
        packages.append({
            "registry": pkg.get("registryType") or pkg.get("registry_name") or pkg.get("registry") or "",
            "identifier": pkg.get("identifier") or pkg.get("name") or "",
            "version": pkg.get("version") or "",
        })

    official = {}
    if isinstance(meta, dict):
        official = meta.get("io.modelcontextprotocol.registry/official") or {}

    metadata = {
        "version": version,
        "repository_url": repo_url,
        "repository_source": repo.get("source") if isinstance(repo, dict) else "",
        "packages": packages,
        "remotes": [r.get("url") for r in (server.get("remotes") or []) if isinstance(r, dict)],
        "registry_status": official.get("status", ""),
        "registry_is_latest": official.get("isLatest"),
        "registry_published_at": official.get("publishedAt", ""),
        "registry_updated_at": official.get("updatedAt", ""),
        "schema": server.get("$schema", ""),
    }

    first_seen_ts = official.get("publishedAt") or datetime.now(timezone.utc).isoformat()
    now_ts = datetime.now(timezone.utc).isoformat()

    return {
        "server_id": compute_server_id(name),
        "name": name,
        "registry_source": REGISTRY_SOURCE,
        "url": url,
        "description": description,
        "trust_score": 0.0,
        "verdict": "unknown",
        "verdict_reasoning": "",
        "confidence": 0.0,
        "last_assessed": None,
        "first_seen": first_seen_ts,
        "last_seen": now_ts,
        "last_scanned": None,
        "scan_count": 0,
        "risk_tier": "unassessed",
        "metadata": json.dumps(metadata),
    }


def normalize_page(page):
    """Normalize every server in a page. Skips non-latest versions so we keep one
    row per server (the registry emits a row per version). Returns list of rows."""
    rows = []
    seen_in_page = set()
    for entry in (page.get("servers") or []):
        _, meta = _server_block(entry)
        official = (meta or {}).get("io.modelcontextprotocol.registry/official") or {}
        # Prefer the latest version; if isLatest is absent, keep it.
        is_latest = official.get("isLatest")
        if is_latest is False:
            continue
        row = normalize_entry(entry)
        if not row:
            continue
        if row["server_id"] in seen_in_page:
            continue
        seen_in_page.add(row["server_id"])
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# dedup + write
# --------------------------------------------------------------------------- #
def existing_server_ids(server_ids):
    """Return the subset of server_ids already present in mcp_server_registry
    (under ANY source) so we never duplicate a server discovered elsewhere."""
    if not server_ids:
        return set()
    quoted = ",".join("'" + sid.replace("'", "''") + "'" for sid in server_ids)
    result = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE server_id IN ({quoted})")
    found = set()
    for r in result.get("rows", []):
        if isinstance(r, dict):
            found.add(r.get("server_id"))
        elif isinstance(r, (list, tuple)) and r:
            found.add(r[0])
    return found


def write_new_rows(rows):
    """Dedup against existing registry rows, write only the net-new ones.
    Returns (new_count, existing_count)."""
    if not rows:
        return 0, 0
    ids = [r["server_id"] for r in rows]
    present = existing_server_ids(ids)
    new_rows = [r for r in rows if r["server_id"] not in present]
    existing = len(rows) - len(new_rows)
    if new_rows:
        res = ws_write("mcp_server_registry", new_rows)
        if not res or res.get("ok") is False:
            log("warn: ws_write for new rows did not confirm ok")
    return len(new_rows), existing


# --------------------------------------------------------------------------- #
# cycle
# --------------------------------------------------------------------------- #
def run_cycle():
    state = load_state()
    cursor = state.get("cursor")
    # Steady state: if we've completed a full sweep, only pull what changed since.
    updated_since = None
    if cursor is None and state.get("last_full_sweep_at"):
        updated_since = state["last_full_sweep_at"]

    pages = 0
    total_seen = 0
    total_new = 0
    total_existing = 0
    errors = 0

    while pages < MAX_PAGES_PER_CYCLE and not stop_event.is_set():
        page = fetch_page(cursor=cursor, updated_since=updated_since)
        if page is None:
            errors += 1
            break

        servers = page.get("servers") or []
        total_seen += len(servers)
        rows = normalize_page(page)
        new_count, existing_count = write_new_rows(rows)
        total_new += new_count
        total_existing += existing_count

        pages += 1
        next_cursor = extract_next_cursor(page)

        if not next_cursor:
            # end of catalog -> mark full sweep complete, reset cursor
            state["cursor"] = None
            state["last_full_sweep_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            log("info: full registry sweep complete")
            cursor = None
            break

        cursor = next_cursor
        state["cursor"] = cursor
        save_state(state)
        time.sleep(FETCH_DELAY_MS / 1000.0)

    log(f"cycle done pages={pages} seen={total_seen} new={total_new} "
        f"existing={total_existing} errors={errors} updated_since={updated_since}")


def run():
    log(f"info: starting {SERVICE_NAME}")
    check_single_instance()
    setup_signals()
    start_heartbeat()
    try:
        while not stop_event.is_set():
            run_cycle()
            stop_event.wait(timeout=POLL_SECS)
    except Exception as e:
        log(f"error: main loop exception: {e}")
    finally:
        remove_pid_file(LOCK_FILE)
        log(f"info: {SERVICE_NAME} stopped")


# --------------------------------------------------------------------------- #
# CLI: --dry-run / --self-test
# --------------------------------------------------------------------------- #
SELF_TEST_FIXTURE = {
    "servers": [
        {
            "server": {
                "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                "name": "ac.tandem/docs-mcp",
                "description": "Remote MCP server for Tandem docs, install guides, SDKs, workflows.",
                "repository": {"url": "https://github.com/frumu-ai/tandem", "source": "github"},
                "version": "0.3.0",
                "remotes": [{"type": "streamable-http", "url": "https://tandem.ac/mcp"}],
            },
            "_meta": {"io.modelcontextprotocol.registry/official": {
                "status": "active", "isLatest": True,
                "publishedAt": "2026-04-02T11:22:40Z", "updatedAt": "2026-04-02T11:22:40Z"}},
        },
        {
            "server": {
                "name": "io.github.example/files-mcp",
                "description": "Local filesystem MCP server distributed on npm and PyPI.",
                "repository": {"url": "https://github.com/example/files-mcp", "source": "github"},
                "version": "2.1.0",
                "packages": [
                    {"registryType": "npm", "identifier": "@example/files-mcp", "version": "2.1.0"},
                    {"registryType": "pypi", "identifier": "example-files-mcp", "version": "2.1.0"},
                ],
            },
            "_meta": {"io.modelcontextprotocol.registry/official": {
                "status": "active", "isLatest": True, "publishedAt": "2026-05-01T00:00:00Z"}},
        },
        {  # an older version of the FIRST server -> must be filtered out (isLatest False)
            "server": {"name": "ac.tandem/docs-mcp", "description": "old", "version": "0.2.0"},
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}},
        },
        {  # remote-only, no repo -> url should fall back to the remote endpoint
            "server": {
                "name": "ac.inference.sh/mcp",
                "description": "Run 150+ AI apps - image, video, audio, LLMs, 3D and more.",
                "version": "1.0.1",
                "remotes": [{"type": "streamable-http", "url": "https://api.inference.sh/mcp"}],
            },
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
        },
    ],
    "metadata": {"nextCursor": "ac.inference.sh/mcp:1.0.1", "count": 4},
}


def self_test():
    """Validate parsing/normalization over a captured sample page. No network/db."""
    failures = []

    rows = normalize_page(SELF_TEST_FIXTURE)

    # 3 latest servers (the isLatest=False duplicate of docs-mcp dropped)
    if len(rows) != 3:
        failures.append(f"expected 3 latest rows, got {len(rows)}: {[r['name'] for r in rows]}")

    by_name = {r["name"]: r for r in rows}

    # every row well-formed
    required = {"server_id", "name", "registry_source", "url", "description", "metadata"}
    for r in rows:
        missing = required - set(r.keys())
        if missing:
            failures.append(f"row {r.get('name')} missing keys {missing}")
        if r["registry_source"] != "mcp_registry":
            failures.append(f"row {r['name']} wrong source {r['registry_source']}")
        # server_id must be deterministic md5 of mcp_registry|name
        if r["server_id"] != compute_server_id(r["name"]):
            failures.append(f"row {r['name']} server_id not stable")
        # metadata must be valid JSON
        try:
            json.loads(r["metadata"])
        except Exception as e:
            failures.append(f"row {r['name']} metadata not valid JSON: {e}")

    # repo url precedence
    t = by_name.get("ac.tandem/docs-mcp")
    if t and t["url"] != "https://github.com/frumu-ai/tandem":
        failures.append(f"docs-mcp url should be repo, got {t['url']}")

    # remote fallback url
    inf = by_name.get("ac.inference.sh/mcp")
    if inf and inf["url"] != "https://api.inference.sh/mcp":
        failures.append(f"inference url should fall back to remote, got {inf['url']}")

    # packages captured
    f = by_name.get("io.github.example/files-mcp")
    if f:
        md = json.loads(f["metadata"])
        if len(md.get("packages", [])) != 2:
            failures.append(f"files-mcp should have 2 packages, got {md.get('packages')}")

    # cursor extraction
    if extract_next_cursor(SELF_TEST_FIXTURE) != "ac.inference.sh/mcp:1.0.1":
        failures.append("extract_next_cursor failed")

    # stable id distinctness
    ids = {r["server_id"] for r in rows}
    if len(ids) != len(rows):
        failures.append("server_ids not distinct across rows")

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print("  - " + f)
        return False

    print(f"SELF-TEST PASSED: {len(rows)} valid rows normalized from sample page")
    for r in rows:
        md = json.loads(r["metadata"])
        print(f"  [{r['server_id'][:8]}] {r['name']}  url={r['url']}  pkgs={len(md.get('packages', []))}")
    return True


def dry_run():
    """Fetch ONE live page (read-only) and report how many NEW vs existing it would
    add. Falls back to the offline fixture if the network/db is unreachable."""
    print(f"[dry-run] fetching one page from {REGISTRY_BASE}{SERVERS_PATH} ...")
    page = fetch_page()
    if page is None:
        print("[dry-run] network fetch failed; running offline parser self-test instead")
        return self_test()
    rows = normalize_page(page)
    print(f"[dry-run] page has {len(page.get('servers', []))} version-rows -> "
          f"{len(rows)} latest servers after de-versioning")
    try:
        ids = [r["server_id"] for r in rows]
        present = existing_server_ids(ids)
        new = [r for r in rows if r["server_id"] not in present]
        print(f"[dry-run] WOULD ADD {len(new)} NEW, skip {len(rows) - len(new)} already-present "
              f"(deduped on server_id)")
    except Exception as e:
        print(f"[dry-run] db dedup check unavailable ({e}); parsed {len(rows)} rows OK")
    print(f"[dry-run] nextCursor={extract_next_cursor(page)!r}")
    return True


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    elif "--dry-run" in sys.argv:
        sys.exit(0 if dry_run() else 1)
    else:
        run()
