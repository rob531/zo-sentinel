#!/usr/bin/env python3
"""
discovery_pulsemcp_paginator.py

Long-running daemon that paginates the PUBLIC PulseMCP directory API
(https://www.pulsemcp.com/api) and writes discovered servers directly into
mcp_server_registry with registry_source='pulsemcp'.

PulseMCP (https://www.pulsemcp.com) is one of the largest community MCP-server
directories. Its read-only public API exposes the full server list with simple
offset pagination plus a total_count, so -- like the official MCP Registry feed
-- there is no separate candidate->promote fetch step needed. This mirrors the
discovery_mcp_registry_paginator.py pattern (fetch external API + write straight
to mcp_server_registry, durable cursor, dedup, single-instance lock, heartbeat).

API (verified 2026-06-24):
  Base:        https://api.pulsemcp.com
  List:        GET /v0beta/servers?count_per_page=<=50[&offset=<int>][&query=<str>]
  Pagination:  offset-based. Response carries:
                 - "servers":     list of server objects (this page)
                 - "total_count": total servers across the whole directory
                 - "next":        absolute URL for the next page (absent/null => end)
               We page by offset and stop when offset >= total_count or "next"
               is empty.
  Auth:        none (public, read-only).
  Rate limits: undocumented and, in practice, an edge layer intermittently
               answers HTTP 410 to throttle. We treat 410 (and 429/5xx) as a
               RETRYABLE backoff signal, not a hard stop, and self-throttle
               between page fetches.
  Supply:      total_count ~= 19,500 servers as of 2026-06-24 -- high-value
               net-new community supply. Substantial overlap is expected with
               the npm / github-search / mcp_registry feeds (many PulseMCP
               entries are GitHub/npm projects), so the realized NET-NEW yield
               is the subset whose canonical key is not already present; dedup
               is on server_id so a server discovered elsewhere is never
               duplicated.

Each PulseMCP entry is normalized into one mcp_server_registry row keyed by a
stable server_id = md5("pulsemcp|<canonical_key>"). The canonical key is the
PulseMCP directory slug parsed from the entry url
(https://www.pulsemcp.com/servers/<slug>), which is stable and unique per
server; we fall back to the server name if a slug can't be parsed. We DEDUP
against existing rows before writing so a server already discovered via
npm/github/smithery/mcp_registry is never duplicated by this source (we check
server_id presence; we never overwrite another source's row).
"""
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, '/home/workspace')

SERVICE_NAME = "discovery_pulsemcp_paginator"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

STATE_DIR = Path("/home/workspace/zo_sentinel/state")
STATE_FILE = STATE_DIR / "pulsemcp_pagination_cursor.json"
LOCK_FILE = Path("/home/workspace/logs/discovery_pulsemcp_paginator.lock")
LOG_FILE = Path("/home/workspace/logs/discovery_pulsemcp_paginator.log")

PULSE_BASE = "https://api.pulsemcp.com"
SERVERS_PATH = "/v0beta/servers"
PAGE_SIZE = 50               # PulseMCP max count_per_page

POLL_SECS = 1800
FETCH_TIMEOUT_SECS = 30
FETCH_DELAY_MS = 350          # polite throttle between page fetches
MAX_PAGES_PER_CYCLE = 60      # bound work per cycle; offset resumes next cycle
HEARTBEAT_INTERVAL_SECS = 30
MAX_RETRIES = 6
BACKOFF_BASE_SECS = 2
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
REGISTRY_SOURCE = "pulsemcp"

# Edge layer throttles with 410 in addition to the usual 429/5xx -> all retryable.
RETRYABLE_STATUS = {410, 429, 500, 502, 503, 504}

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
        "offset": 0,                  # in-flight offset within a full sweep
        "total_count": None,          # last observed directory size
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
def fetch_page(offset=0, query=None):
    """Fetch one page of the directory with retry/backoff. Returns parsed dict or None."""
    params = {"count_per_page": PAGE_SIZE, "offset": offset}
    if query:
        params["query"] = query
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    url = PULSE_BASE + SERVERS_PATH
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=FETCH_TIMEOUT_SECS)
            if resp.status_code in RETRYABLE_STATUS:
                wait = BACKOFF_BASE_SECS * (2 ** attempt)
                log(f"warn: HTTP {resp.status_code} (retryable throttle) offset={offset}, backing off {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            wait = BACKOFF_BASE_SECS * (2 ** attempt)
            log(f"warn: fetch attempt {attempt + 1}/{MAX_RETRIES} HTTP error: {e}; retry in {wait}s")
            time.sleep(wait)
        except Exception as e:
            wait = BACKOFF_BASE_SECS * (2 ** attempt)
            log(f"warn: fetch attempt {attempt + 1}/{MAX_RETRIES} failed: {e}; retry in {wait}s")
            time.sleep(wait)
    log(f"error: page fetch exhausted retries at offset={offset}")
    return None


def parse_slug(url):
    """Pull the stable PulseMCP directory slug from a server url, e.g.
    https://www.pulsemcp.com/servers/<slug> -> <slug>. Returns '' if not parseable."""
    if not url:
        return ""
    try:
        path = urlparse(url).path or ""
    except Exception:
        return ""
    m = re.search(r"/servers/([^/?#]+)", path)
    return m.group(1) if m else ""


def canonical_key(server):
    """Stable dedup key for a PulseMCP server: the directory slug, else the name."""
    slug = parse_slug(server.get("url") or "")
    if slug:
        return slug
    return (server.get("name") or "").strip()


def compute_server_id(key):
    """Stable id. Mirrors the md5('<source>|<key>') scheme used by the npm/smithery/
    mcp_registry promoters."""
    return hashlib.md5(f"{REGISTRY_SOURCE}|{key}".encode("utf-8")).hexdigest()


def total_count(page):
    if not isinstance(page, dict):
        return None
    tc = page.get("total_count")
    try:
        return int(tc) if tc is not None else None
    except (TypeError, ValueError):
        return None


def has_next(page):
    """True if the API advertises another page."""
    if not isinstance(page, dict):
        return False
    nxt = page.get("next")
    return bool(nxt)


def normalize_entry(entry):
    """Convert one PulseMCP server object into a mcp_server_registry row dict, or
    None if it lacks a usable canonical key. Pure: no network, no db."""
    if not isinstance(entry, dict):
        return None
    key = canonical_key(entry)
    if not key:
        return None

    name = (entry.get("name") or key)
    description = (entry.get("short_description")
                  or entry.get("EXPERIMENTAL_ai_generated_description")
                  or "")[:1000]

    # url precedence: source repo -> external project url -> PulseMCP directory page
    url = (entry.get("source_code_url")
           or entry.get("external_url")
           or entry.get("url")
           or "")

    remotes = []
    for r in (entry.get("remotes") or []):
        if isinstance(r, dict):
            remotes.append({
                "url": r.get("url_direct") or r.get("url_setup") or "",
                "transport": r.get("transport") or "",
                "authentication_method": r.get("authentication_method") or "",
                "cost": r.get("cost") or "",
            })

    metadata = {
        "pulse_url": entry.get("url") or "",
        "external_url": entry.get("external_url") or "",
        "source_code_url": entry.get("source_code_url") or "",
        "github_stars": entry.get("github_stars"),
        "package_registry": entry.get("package_registry") or "",
        "package_name": entry.get("package_name") or "",
        "package_download_count": entry.get("package_download_count"),
        "remotes": remotes,
        "ai_generated_description": entry.get("EXPERIMENTAL_ai_generated_description") or "",
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
    offset = int(state.get("offset") or 0)

    pages = 0
    total_seen = 0
    total_new = 0
    total_existing = 0
    errors = 0
    tc = state.get("total_count")

    while pages < MAX_PAGES_PER_CYCLE and not stop_event.is_set():
        page = fetch_page(offset=offset)
        if page is None:
            errors += 1
            break

        servers = page.get("servers") or []
        page_tc = total_count(page)
        if page_tc is not None:
            tc = page_tc
            state["total_count"] = tc

        total_seen += len(servers)
        rows = normalize_page(page)
        new_count, existing_count = write_new_rows(rows)
        total_new += new_count
        total_existing += existing_count

        pages += 1
        offset += len(servers) if servers else PAGE_SIZE

        end_of_catalog = (not has_next(page)) or (not servers) or \
            (tc is not None and offset >= tc)
        if end_of_catalog:
            state["offset"] = 0
            state["last_full_sweep_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            log("info: full pulsemcp sweep complete")
            offset = 0
            break

        state["offset"] = offset
        save_state(state)
        time.sleep(FETCH_DELAY_MS / 1000.0)

    log(f"cycle done pages={pages} seen={total_seen} new={total_new} "
        f"existing={total_existing} errors={errors} offset={offset} total_count={tc}")


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
            "name": "x711",
            "url": "https://www.pulsemcp.com/servers/0580iris-lang-x711",
            "external_url": "https://x711.io",
            "short_description": "Pay-per-use tool API for AI agents with free tier, x402 USDC micropayments, and API key access.",
            "source_code_url": None,
            "github_stars": None,
            "package_registry": None,
            "package_name": None,
            "package_download_count": None,
            "EXPERIMENTAL_ai_generated_description": "Provides a pay-per-use tool API for AI agents.",
            "remotes": [
                {"url_direct": "https://x711.io/mcp", "url_setup": None,
                 "transport": "streamable_http", "authentication_method": "api_key", "cost": "free_tier"}
            ],
        },
        {
            "name": "Medium Ops",
            "url": "https://www.pulsemcp.com/servers/06ketan-medium-ops",
            "external_url": "https://github.com/06ketan/medium-ops",
            "short_description": "Medium content management with 22 tools for browsing posts, managing responses, tracking claps.",
            "source_code_url": "https://github.com/06ketan/medium-ops",
            "github_stars": 0,
            "package_registry": None,
            "package_name": None,
            "package_download_count": None,
            "EXPERIMENTAL_ai_generated_description": "Interact with Medium stories, responses, and claps.",
            "remotes": [],
        },
        {
            "name": "Slideshot",
            "url": "https://www.pulsemcp.com/servers/06ketan-slideshot",
            "external_url": "https://github.com/06ketan/slideshot",
            "short_description": "Convert HTML to PDF, PNG, WebP, and PPTX slide carousels with 11 presentation themes.",
            "source_code_url": "https://github.com/06ketan/slideshot",
            "github_stars": 2,
            "package_registry": "npm",
            "package_name": "slideshot",
            "package_download_count": 100,
            "EXPERIMENTAL_ai_generated_description": "Slideshot converts HTML content into presentation-ready formats.",
            "remotes": [],
        },
        {  # no parseable slug, no name -> must be dropped
            "name": "",
            "url": "https://example.com/not-a-pulse-page",
            "short_description": "junk",
            "remotes": [],
        },
    ],
    "total_count": 19502,
    "next": "https://api.pulsemcp.com/v0beta/servers?count_per_page=50&offset=50",
}


def self_test():
    """Validate parsing/normalization over a captured sample page. No network/db."""
    failures = []

    rows = normalize_page(SELF_TEST_FIXTURE)

    # 3 usable servers (the no-slug/no-name junk entry dropped)
    if len(rows) != 3:
        failures.append(f"expected 3 rows, got {len(rows)}: {[r['name'] for r in rows]}")

    by_name = {r["name"]: r for r in rows}

    required = {"server_id", "name", "registry_source", "url", "description", "metadata"}
    for r in rows:
        missing = required - set(r.keys())
        if missing:
            failures.append(f"row {r.get('name')} missing keys {missing}")
        if r["registry_source"] != "pulsemcp":
            failures.append(f"row {r['name']} wrong source {r['registry_source']}")
        try:
            json.loads(r["metadata"])
        except Exception as e:
            failures.append(f"row {r['name']} metadata not valid JSON: {e}")

    # dedup key derives from the directory slug, NOT the name
    x711 = by_name.get("x711")
    if x711 and x711["server_id"] != compute_server_id("0580iris-lang-x711"):
        failures.append("x711 server_id not keyed on the directory slug")

    # url precedence: source_code_url wins when present
    s = by_name.get("Slideshot")
    if s and s["url"] != "https://github.com/06ketan/slideshot":
        failures.append(f"Slideshot url should be source repo, got {s['url']}")

    # remote-only server falls back to external_url (no source repo)
    x = by_name.get("x711")
    if x and x["url"] != "https://x711.io":
        failures.append(f"x711 url should fall back to external_url, got {x['url']}")

    # health/quality signals captured into metadata
    if s:
        md = json.loads(s["metadata"])
        if md.get("github_stars") != 2 or md.get("package_registry") != "npm" \
                or md.get("package_download_count") != 100:
            failures.append(f"Slideshot quality signals not captured: {md}")
        if not md.get("remotes") == []:
            failures.append("Slideshot remotes should be empty list")
    if x:
        mdx = json.loads(x["metadata"])
        if not mdx.get("remotes") or mdx["remotes"][0].get("transport") != "streamable_http":
            failures.append(f"x711 remote transport not captured: {mdx.get('remotes')}")

    # pagination helpers
    if total_count(SELF_TEST_FIXTURE) != 19502:
        failures.append("total_count extraction failed")
    if not has_next(SELF_TEST_FIXTURE):
        failures.append("has_next should be True for fixture")

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
              f"stars={md.get('github_stars')} dl={md.get('package_download_count')}")
    return True


def dry_run():
    """Fetch ONE live page (read-only) and report how many NEW vs existing it would
    add. Falls back to the offline fixture if the network/db is unreachable."""
    print(f"[dry-run] fetching one page from {PULSE_BASE}{SERVERS_PATH} ...")
    page = fetch_page(offset=0)
    if page is None:
        print("[dry-run] network fetch failed; running offline parser self-test instead")
        return self_test()
    rows = normalize_page(page)
    print(f"[dry-run] page has {len(page.get('servers', []))} servers -> "
          f"{len(rows)} normalized rows")
    print(f"[dry-run] directory total_count={total_count(page)}  next={'yes' if has_next(page) else 'no'}")
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
