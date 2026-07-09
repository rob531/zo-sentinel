#!/usr/bin/env python3
"""
discovery_glama_paginator.py

Long-running daemon that paginates the PUBLIC Glama MCP registry API
(https://glama.ai/api/mcp/v1/servers) and writes discovered servers directly
into mcp_server_registry with registry_source='glama'.

Glama (https://glama.ai/mcp/servers) is a large MCP-server registry that, unlike
the bare directories, attaches health/quality signals to each entry (hosting
attributes, SPDX license, repository, declared tools, declared environment
variables). Its read-only public API exposes the full server list with opaque
CURSOR pagination, so -- like the official MCP Registry feed -- there is no
separate candidate->promote fetch step. This mirrors the
discovery_mcp_registry_paginator.py pattern (fetch external API + write straight
to mcp_server_registry, durable cursor, dedup, single-instance lock, heartbeat).

API (verified 2026-06-24):
  Base:        https://glama.ai
  List:        GET /api/mcp/v1/servers?first=<=100[&after=<opaque-cursor>]
  Pagination:  Relay-style cursor. Response carries:
                 - "servers":  list of server objects (this page)
                 - "pageInfo": {endCursor, hasNextPage, startCursor, hasPreviousPage}
               Pass pageInfo.endCursor as ?after=... to get the next page; stop
               when hasNextPage is false.
  Auth:        none for the public registry list (read-only).
  Rate limits: undocumented/generous; we self-throttle (FETCH_DELAY_MS) and back
               off on errors regardless.
  Fields:      id, name, namespace, slug, description, repository{url},
               spdxLicense{name,url}, attributes[] (e.g. "hosting:local-only",
               "hosting:remote-capable", "hosting:hybrid"), tools[],
               environmentVariablesJsonSchema, url (glama web page).
  Supply:      tens of thousands of servers in the Glama registry -- high-value
               net-new supply WITH quality signals. Substantial overlap is
               expected with the github-search / mcp_registry / npm feeds (most
               Glama entries carry a GitHub repository), so realized NET-NEW is
               the subset whose canonical key is not already present; dedup is
               on server_id so a server discovered elsewhere is never duplicated.

Each Glama entry is normalized into one mcp_server_registry row keyed by a stable
server_id = md5("glama|<canonical_key>"). The canonical key is Glama's opaque
server id (e.g. "t17ktgyhpx"), which is stable and unique; we fall back to
"<namespace>/<slug>" then to the name if the id is absent. We DEDUP against
existing rows before writing so a server already discovered via
npm/github/smithery/mcp_registry/pulsemcp is never duplicated by this source (we
check server_id presence; we never overwrite another source's row).

The health/quality signals (hosting attributes, license, declared tool count,
declared env-var count, repository) are captured into the row's metadata JSON for
later trust scoring.
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

SERVICE_NAME = "discovery_glama_paginator"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

STATE_DIR = Path("/home/workspace/zo_sentinel/state")
STATE_FILE = STATE_DIR / "glama_pagination_cursor.json"
LOCK_FILE = Path("/home/workspace/logs/discovery_glama_paginator.lock")
LOG_FILE = Path("/home/workspace/logs/discovery_glama_paginator.log")

GLAMA_BASE = "https://glama.ai"
SERVERS_PATH = "/api/mcp/v1/servers"
PAGE_SIZE = 100              # Glama 'first' page size

POLL_SECS = 1800
FETCH_TIMEOUT_SECS = 30
FETCH_DELAY_MS = 300          # polite throttle between page fetches
MAX_PAGES_PER_CYCLE = 60      # bound work per cycle; cursor resumes next cycle
HEARTBEAT_INTERVAL_SECS = 30
MAX_RETRIES = 5
BACKOFF_BASE_SECS = 2
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
REGISTRY_SOURCE = "glama"

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

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
        except (OSError, SystemError):  # Windows os.kill(pid,0) raises SystemError, not OSError
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
        "cursor": None,               # in-flight after-cursor within a full sweep
        "last_full_sweep_at": None,   # RFC3339 of last completed full sweep
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
def fetch_page(after=None):
    """Fetch one page of the registry with retry/backoff. Returns parsed dict or None."""
    params = {"first": PAGE_SIZE}
    if after:
        params["after"] = after
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    url = GLAMA_BASE + SERVERS_PATH
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=FETCH_TIMEOUT_SECS)
            if resp.status_code in RETRYABLE_STATUS:
                wait = BACKOFF_BASE_SECS * (2 ** attempt)
                log(f"warn: HTTP {resp.status_code} (retryable), backing off {wait}s")
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


def extract_page_info(page):
    """Return (end_cursor, has_next) from a Glama pageInfo block."""
    if not isinstance(page, dict):
        return None, False
    info = page.get("pageInfo") or {}
    end_cursor = info.get("endCursor")
    has_next = bool(info.get("hasNextPage"))
    return end_cursor, has_next


def canonical_key(server):
    """Stable dedup key for a Glama server: the opaque id, else namespace/slug,
    else the name."""
    sid = (server.get("id") or "").strip()
    if sid:
        return sid
    ns = (server.get("namespace") or "").strip()
    slug = (server.get("slug") or "").strip()
    if ns and slug:
        return f"{ns}/{slug}"
    if slug:
        return slug
    return (server.get("name") or "").strip()


def compute_server_id(key):
    """Stable id. Mirrors the md5('<source>|<key>') scheme used by the npm/smithery/
    mcp_registry promoters."""
    return hashlib.md5(f"{REGISTRY_SOURCE}|{key}".encode("utf-8")).hexdigest()


def _hosting_from_attributes(attributes):
    """Pull the 'hosting:*' value out of the attributes list, if any."""
    for a in (attributes or []):
        if isinstance(a, str) and a.startswith("hosting:"):
            return a.split(":", 1)[1]
    return ""


def normalize_entry(entry):
    """Convert one Glama server object into a mcp_server_registry row dict, or None
    if it lacks a usable canonical key. Pure: no network, no db."""
    if not isinstance(entry, dict):
        return None
    key = canonical_key(entry)
    if not key:
        return None

    name = (entry.get("name") or entry.get("slug") or key)
    description = (entry.get("description") or "")[:1000]

    repo = entry.get("repository") or {}
    repo_url = ""
    if isinstance(repo, dict):
        repo_url = repo.get("url") or ""
    elif isinstance(repo, str):
        repo_url = repo

    # url precedence: repository -> Glama web page
    url = repo_url or entry.get("url") or f"{GLAMA_BASE}/mcp/servers/{key}"

    attributes = entry.get("attributes") or []
    spdx = entry.get("spdxLicense") or {}
    # Glama's list AND detail endpoints return an EMPTY tools[] for any server
    # it cannot introspect (BYO-backend, unpublished, private-prep). The old
    # `len(tools or [])` published a FABRICATED "0 tools" that reads as fact --
    # e.g. sap-mcp-server (~13 real tools) surfaced as tool_count:0 (confusion
    # found 2026-07-04). THE LINE: never publish a fabricated value. Only record
    # a count when Glama actually returned tools/env; otherwise it is UNKNOWN
    # (None), flagged, so nothing downstream can mistake unknown for zero.
    tools = entry.get("tools")
    env_schema = entry.get("environmentVariablesJsonSchema") or {}
    env_props = None
    if isinstance(env_schema, dict):
        env_props = env_schema.get("properties")
    tool_count = len(tools) if isinstance(tools, list) and tools else None
    env_var_count = len(env_props) if isinstance(env_props, dict) and env_props else None

    # health / quality signals -> kept for later trust scoring
    metadata = {
        "glama_id": entry.get("id") or "",
        "namespace": entry.get("namespace") or "",
        "slug": entry.get("slug") or "",
        "glama_url": entry.get("url") or "",
        "repository_url": repo_url,
        "attributes": attributes,
        "hosting": _hosting_from_attributes(attributes),
        "spdx_license": (spdx.get("name") if isinstance(spdx, dict) else "") or "",
        "spdx_license_url": (spdx.get("url") if isinstance(spdx, dict) else "") or "",
        "has_license": bool(isinstance(spdx, dict) and spdx.get("name")),
        "tool_count": tool_count,
        "tool_count_verified": tool_count is not None,
        "env_var_count": env_var_count,
        "env_var_count_verified": env_var_count is not None,
    }

    now_ts = datetime.now(timezone.utc).isoformat()

    return {
        "server_id": compute_server_id(key),
        "name": name,
        "registry_source": REGISTRY_SOURCE,
        "url": url,
        "description": description,
        "trust_score": 0.0,
        "verdict": "unknown",
        "verdict_reasoning": "",
        "confidence": 0.0,
        "last_assessed": None,
        "first_seen": now_ts,
        "last_seen": now_ts,
        "last_scanned": None,
        "scan_count": 0,
        "risk_tier": "unassessed",
        "metadata": json.dumps(metadata),
    }


def normalize_page(page):
    """Normalize every server in a page. De-dups within the page on server_id.
    Returns list of rows."""
    rows = []
    seen_in_page = set()
    for entry in (page.get("servers") or []):
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

    pages = 0
    total_seen = 0
    total_new = 0
    total_existing = 0
    errors = 0

    while pages < MAX_PAGES_PER_CYCLE and not stop_event.is_set():
        page = fetch_page(after=cursor)
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
        end_cursor, has_next = extract_page_info(page)

        if not has_next or not end_cursor:
            # end of catalog -> mark full sweep complete, reset cursor
            state["cursor"] = None
            state["last_full_sweep_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            log("info: full glama sweep complete")
            cursor = None
            break

        cursor = end_cursor
        state["cursor"] = cursor
        save_state(state)
        time.sleep(FETCH_DELAY_MS / 1000.0)

    log(f"cycle done pages={pages} seen={total_seen} new={total_new} "
        f"existing={total_existing} errors={errors}")


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
    "pageInfo": {
        "endCursor": "eyJjcmVhdGVkQXQiOjE3ODIzMjQyNjAsImlkIjoib3B3OXZyNzNveiJ9",
        "hasNextPage": True,
        "startCursor": "eyJjcmVhdGVkQXQiOjE3ODIzMjQ0MzksImlkIjoidDE3a3RneWhweCJ9",
        "hasPreviousPage": False,
    },
    "servers": [
        {
            "attributes": ["hosting:local-only"],
            "description": "MCP server and CLI for detecting, redacting, and auditing PHI in medical text.",
            "environmentVariablesJsonSchema": {"properties": {}, "type": "object", "required": []},
            "id": "t17ktgyhpx",
            "name": "phi-guard-mcp",
            "namespace": "dcl632",
            "repository": {"url": "https://github.com/dcl632/phi-guard-mcp"},
            "slug": "phi-guard-mcp",
            "spdxLicense": {"name": "MIT License", "url": "https://spdx.org/licenses/MIT.json"},
            "tools": [],
            "url": "https://glama.ai/mcp/servers/t17ktgyhpx",
        },
        {
            "attributes": ["hosting:hybrid"],
            "description": "MCP server that exposes Yahoo Finance data through tools for quotes, history, news.",
            "environmentVariablesJsonSchema": {
                "properties": {
                    "YF_MCP_HOST": {"type": "string", "default": "127.0.0.1"},
                    "YF_MCP_PORT": {"type": "string", "default": "8000"},
                    "YF_MCP_TRANSPORT": {"type": "string", "default": "stdio"},
                },
                "type": "object", "required": [],
            },
            "id": "opw9vr73oz",
            "name": "Yahoo Finance MCP Server",
            "namespace": "benethos-hub",
            "repository": {"url": "https://github.com/benethos-hub/yahoo-finance-mcp"},
            "slug": "yahoo-finance-mcp",
            "spdxLicense": {"name": "MIT License", "url": "https://spdx.org/licenses/MIT.json"},
            "tools": [{"name": "get_quote"}, {"name": "get_history"}],
            "url": "https://glama.ai/mcp/servers/opw9vr73oz",
        },
        {  # no license, remote-capable, no repository -> url falls back to glama page
            "attributes": ["hosting:remote-capable"],
            "description": "Log reader MCP.",
            "id": "ehvr0dhhdz",
            "name": "log-reader-mcp",
            "namespace": "ankit-jhajhria",
            "slug": "log-reader-mcp",
            "spdxLicense": None,
            "tools": [],
            "url": "https://glama.ai/mcp/servers/ehvr0dhhdz",
        },
        {  # no id, no slug, no name -> dropped
            "attributes": [],
            "description": "junk",
        },
    ],
}


def self_test():
    """Validate parsing/normalization over a captured sample page. No network/db."""
    failures = []

    rows = normalize_page(SELF_TEST_FIXTURE)

    # 3 usable servers (the keyless junk entry dropped)
    if len(rows) != 3:
        failures.append(f"expected 3 rows, got {len(rows)}: {[r['name'] for r in rows]}")

    by_name = {r["name"]: r for r in rows}

    required = {"server_id", "name", "registry_source", "url", "description", "metadata"}
    for r in rows:
        missing = required - set(r.keys())
        if missing:
            failures.append(f"row {r.get('name')} missing keys {missing}")
        if r["registry_source"] != "glama":
            failures.append(f"row {r['name']} wrong source {r['registry_source']}")
        try:
            json.loads(r["metadata"])
        except Exception as e:
            failures.append(f"row {r['name']} metadata not valid JSON: {e}")

    # dedup key derives from the opaque glama id
    phi = by_name.get("phi-guard-mcp")
    if phi and phi["server_id"] != compute_server_id("t17ktgyhpx"):
        failures.append("phi-guard server_id not keyed on the glama id")

    # url precedence: repository wins when present
    if phi and phi["url"] != "https://github.com/dcl632/phi-guard-mcp":
        failures.append(f"phi-guard url should be repo, got {phi['url']}")

    # no-repo server falls back to the glama web page
    lr = by_name.get("log-reader-mcp")
    if lr and lr["url"] != "https://glama.ai/mcp/servers/ehvr0dhhdz":
        failures.append(f"log-reader url should fall back to glama page, got {lr['url']}")

    # health / quality signals captured into metadata
    yf = by_name.get("Yahoo Finance MCP Server")
    if yf:
        md = json.loads(yf["metadata"])
        if md.get("hosting") != "hybrid":
            failures.append(f"yf hosting not captured: {md.get('hosting')}")
        if md.get("tool_count") != 2:
            failures.append(f"yf tool_count should be 2, got {md.get('tool_count')}")
        if md.get("env_var_count") != 3:
            failures.append(f"yf env_var_count should be 3, got {md.get('env_var_count')}")
        if md.get("spdx_license") != "MIT License" or not md.get("has_license"):
            failures.append(f"yf license not captured: {md}")
    if lr:
        mdl = json.loads(lr["metadata"])
        if mdl.get("hosting") != "remote-capable":
            failures.append(f"log-reader hosting not captured: {mdl.get('hosting')}")
        if mdl.get("has_license") is not False:
            failures.append("log-reader should have has_license False")

    # cursor / pageInfo extraction
    end_cursor, has_next = extract_page_info(SELF_TEST_FIXTURE)
    if end_cursor != "eyJjcmVhdGVkQXQiOjE3ODIzMjQyNjAsImlkIjoib3B3OXZyNzNveiJ9" or not has_next:
        failures.append(f"extract_page_info failed: {end_cursor} {has_next}")

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
        print(f"  [{r['server_id'][:8]}] {r['name']}  url={r['url']}  "
              f"hosting={md.get('hosting')} tools={md.get('tool_count')} lic={md.get('spdx_license')}")
    return True


def dry_run():
    """Fetch ONE live page (read-only) and report how many NEW vs existing it would
    add. Falls back to the offline fixture if the network/db is unreachable."""
    print(f"[dry-run] fetching one page from {GLAMA_BASE}{SERVERS_PATH} ...")
    page = fetch_page()
    if page is None:
        print("[dry-run] network fetch failed; running offline parser self-test instead")
        return self_test()
    rows = normalize_page(page)
    end_cursor, has_next = extract_page_info(page)
    print(f"[dry-run] page has {len(page.get('servers', []))} servers -> "
          f"{len(rows)} normalized rows")
    print(f"[dry-run] hasNextPage={has_next}  endCursor={end_cursor!r}")
    try:
        ids = [r["server_id"] for r in rows]
        present = existing_server_ids(ids)
        new = [r for r in rows if r["server_id"] not in present]
        print(f"[dry-run] WOULD ADD {len(new)} NEW, skip {len(rows) - len(new)} already-present "
              f"(deduped on server_id)")
    except Exception as e:
        print(f"[dry-run] db dedup check unavailable ({e}); parsed {len(rows)} rows OK")
    return True


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    elif "--dry-run" in sys.argv:
        sys.exit(0 if dry_run() else 1)
    else:
        run()
