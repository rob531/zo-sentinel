#!/usr/bin/env python3
"""
mcp_registry_ingestor.py

Ingests the Official MCP Registry at registry.modelcontextprotocol.io.

This is the authoritative community-maintained list of MCP servers,
replacing the legacy README in the modelcontextprotocol/servers repo.
For our trust-intelligence purposes the registry is a lookup:

  * Matches → mcp_directory_mentions with directory_name='mcp_registry'.
              A server we already have is also in the canonical registry:
              baseline existence signal.

  * Misses → mcp_discovery_candidates. Registry knows about it, we don't.
              These are the long-tail entries that matter most for risk
              analysis (operator thesis: unusual MCPs are more useful than
              popular ones). We capture registryType + identifier + version
              so future shape-analysis work has the enumerable data points
              it needs without a re-crawl.

API reference:
  Base URL   : https://registry.modelcontextprotocol.io
  List       : GET /v0/servers?limit=100&version=latest[&cursor=...]
  Pagination : response.metadata.nextCursor (opaque string)
  Response   : {servers: [{server: {name, description, version},
                            _meta: {..official: {status, publishedAt, isLatest}},
                            packages: [{registryType, identifier, version, ...}]}],
                metadata: {count, nextCursor}}

Design decisions (carrying forward from mcp_reference_servers_ingestor.py):
  - /execute for schema + upserts (NOT /write, which auto-injects id)
  - Deterministic MD5 IDs keyed on (directory_name, stable_name)
  - Explicit IN-placeholder expansion for multi-candidate queries
  - Parameterized SQL throughout
  - Schema-first verification (CREATE IF NOT EXISTS every run)
  - One-shot. Run via scheduled task or wrapper, not a daemon loop.

Enumeration discipline:
  - 100 servers per page (API max)
  - Hard page ceiling as a safety brake (MAX_PAGES) — if the registry
    ever balloons to ridiculous size, fail safe rather than run forever
  - version=latest so we don't ingest historical versions as separate rows
  - 250ms inter-page sleep to stay well under any reasonable rate limit

Flags:
  --smoke  Fetch ONE page and print a small sample to stdout.
           No DB writes. Use to verify API shape before committing.
  --limit N  Stop after N total servers processed (useful for pilots).
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

LOG = logging.getLogger("mcp_registry_ingestor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
LIST_ENDPOINT = "/v0/servers"
PAGE_SIZE = 100
MAX_PAGES = 50               # 50 * 100 = 5000 server safety ceiling
INTER_PAGE_SLEEP_SEC = 0.25  # politeness
FETCH_TIMEOUT_SEC = 20
EXECUTE_TIMEOUT_SEC = 10
WRITE_SERVICE_BASE = "http://127.0.0.1:8772"
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
FROM_HEADER = "hello@zocomputer.io"
DIRECTORY_NAME = "mcp_registry"

EXIT_OK = 0
EXIT_FETCH_FAIL = 1
EXIT_PARSE_FAIL = 2
EXIT_WRITE_FAIL = 3


# ---------------------------------------------------------------------------
# Schema bootstrap (shared tables with mcp_reference_servers_ingestor;
# idempotent IF NOT EXISTS so safe to run either ingestor first)
# ---------------------------------------------------------------------------

SCHEMA_MENTIONS = """
CREATE TABLE IF NOT EXISTS mcp_directory_mentions (
    id               BIGINT PRIMARY KEY,
    server_id        VARCHAR,
    directory_name   VARCHAR NOT NULL,
    mention_name     VARCHAR NOT NULL,
    mention_url      VARCHAR,
    mention_context  VARCHAR,
    mention_status   VARCHAR,
    mention_rank     INTEGER,
    first_seen       TIMESTAMPTZ DEFAULT now(),
    last_seen        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (directory_name, mention_name)
)
""".strip()

SCHEMA_CANDIDATES = """
CREATE TABLE IF NOT EXISTS mcp_discovery_candidates (
    id                         BIGINT PRIMARY KEY,
    candidate_name             VARCHAR NOT NULL,
    candidate_url              VARCHAR,
    candidate_description      VARCHAR,
    discovered_in_directory    VARCHAR NOT NULL,
    discovered_status          VARCHAR,
    first_seen                 TIMESTAMPTZ DEFAULT now(),
    last_seen                  TIMESTAMPTZ DEFAULT now(),
    reviewed_at                TIMESTAMPTZ,
    promoted                   BOOLEAN DEFAULT FALSE,
    UNIQUE (discovered_in_directory, candidate_name)
)
""".strip()

# Additional table specific to registry ingestion: preserve the rich
# enumerable metadata (registryType, identifier, version, publishedAt)
# per server so future shape-analysis work doesn't need a re-crawl.
# Keyed on registry name; rows are overwritten each run with latest data.
SCHEMA_REGISTRY_FACTS = """
CREATE TABLE IF NOT EXISTS mcp_registry_facts (
    id                BIGINT PRIMARY KEY,
    registry_name     VARCHAR NOT NULL,
    version           VARCHAR,
    description       VARCHAR,
    status            VARCHAR,
    published_at      TIMESTAMPTZ,
    is_latest         BOOLEAN,
    package_count     INTEGER,
    primary_registry  VARCHAR,
    primary_identifier VARCHAR,
    raw_packages      VARCHAR,
    server_id         VARCHAR,
    first_seen        TIMESTAMPTZ DEFAULT now(),
    last_seen         TIMESTAMPTZ DEFAULT now(),
    UNIQUE (registry_name)
)
""".strip()


# ---------------------------------------------------------------------------
# WriteService helpers
# ---------------------------------------------------------------------------

def ws_execute(sql: str, params: Optional[list] = None) -> dict:
    payload = {"sql": sql, "wait": True}
    if params is not None:
        payload["params"] = params
    r = requests.post(
        WRITE_SERVICE_BASE + "/execute",
        json=payload,
        timeout=EXECUTE_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json()


def ws_query(sql: str, params: Optional[list] = None) -> list:
    payload = {"sql": sql}
    if params is not None:
        payload["params"] = params
    r = requests.post(
        WRITE_SERVICE_BASE + "/query",
        json=payload,
        timeout=EXECUTE_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json().get("rows", [])


def heartbeat() -> None:
    try:
        requests.post(
            WRITE_SERVICE_BASE + "/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "mcp_registry_ingestor",
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception as e:
        LOG.warning("heartbeat failed: %s", e)


def ensure_schema() -> None:
    ws_execute(SCHEMA_MENTIONS)
    ws_execute(SCHEMA_CANDIDATES)
    ws_execute(SCHEMA_REGISTRY_FACTS)


# ---------------------------------------------------------------------------
# Fetch + paginate
# ---------------------------------------------------------------------------

def fetch_page(cursor: Optional[str] = None) -> dict:
    params = {"limit": PAGE_SIZE, "version": "latest"}
    if cursor:
        params["cursor"] = cursor
    headers = {
        "User-Agent": USER_AGENT,
        "From": FROM_HEADER,
        "Accept": "application/json",
    }
    r = requests.get(
        REGISTRY_BASE + LIST_ENDPOINT,
        params=params,
        headers=headers,
        timeout=FETCH_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Entry extraction and normalization
# ---------------------------------------------------------------------------

def extract_entry(raw: dict) -> Optional[dict]:
    """
    Normalize one entry from the registry response into our internal shape.

    The registry response wraps actual server data under 'server':
        { server: {name, description, version},
          _meta:  {..official: {status, publishedAt, isLatest}},
          packages: [{registryType, identifier, version, transport}] }

    Defensive: some of these fields may be missing on individual rows.
    """
    server = raw.get("server") or {}
    name = (server.get("name") or "").strip()
    if not name:
        return None

    meta = raw.get("_meta") or {}
    # Find the 'official' subkey — it's namespaced like 'io.modelcontextprotocol.registry/official'
    official = {}
    for k, v in meta.items():
        if isinstance(v, dict) and ("official" in k or k.endswith("/official")):
            official = v
            break

    packages = raw.get("packages") or []
    primary_registry = None
    primary_identifier = None
    if packages and isinstance(packages, list):
        first = packages[0] or {}
        primary_registry = first.get("registryType") or first.get("registry_type")
        primary_identifier = first.get("identifier")

    return {
        "registry_name": name,
        "description": (server.get("description") or "").strip(),
        "version": server.get("version"),
        "status": official.get("status"),
        "published_at": official.get("publishedAt"),
        "is_latest": bool(official.get("isLatest")) if "isLatest" in official else None,
        "packages": packages,
        "package_count": len(packages),
        "primary_registry": primary_registry,
        "primary_identifier": primary_identifier,
    }


# ---------------------------------------------------------------------------
# Match to registry (mcp_server_registry)
# ---------------------------------------------------------------------------

def _candidate_names_from_entry(entry: dict) -> list:
    """
    Build the set of names likely to appear in mcp_server_registry for this
    registry entry. Strategy:

      1. Every package identifier verbatim (this is the strongest signal —
         our npm/pypi crawlers store by package identifier).
      2. Package identifier with '@scope/' stripped (for scope-insensitive
         matches in case our registry has both forms).
      3. The reverse-DNS registry name trailing segment (e.g.
         'io.modelcontextprotocol/filesystem' → 'filesystem').
    """
    out = []
    for pkg in entry.get("packages") or []:
        ident = (pkg.get("identifier") or "").strip()
        if ident:
            out.append(ident.lower())
            if ident.startswith("@") and "/" in ident:
                out.append(ident.split("/", 1)[1].lower())

    regname = entry.get("registry_name") or ""
    if "/" in regname:
        tail = regname.split("/", 1)[1].strip()
        if tail:
            out.append(tail.lower())

    # Dedupe preserving order
    seen = set()
    deduped = []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            deduped.append(n)
    return deduped


def match_to_registry(entry: dict) -> Optional[dict]:
    candidates = _candidate_names_from_entry(entry)
    if not candidates:
        return None
    placeholders = ", ".join(["?"] * len(candidates))
    rows = ws_query(
        f"SELECT server_id, name, url FROM mcp_server_registry "
        f"WHERE LOWER(name) IN ({placeholders}) LIMIT 1",
        params=candidates,
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _deterministic_id(*parts: str) -> int:
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest()[:8], 16) % (2**31)


def upsert_mention(entry: dict, server_id: str) -> None:
    row_id = _deterministic_id(DIRECTORY_NAME, entry["registry_name"])
    # mention_url: point at the authoritative registry record
    mention_url = f"{REGISTRY_BASE}/v0/servers/{entry['registry_name']}"
    ws_execute(
        """
        INSERT INTO mcp_directory_mentions
            (id, server_id, directory_name, mention_name, mention_url,
             mention_context, mention_status, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (directory_name, mention_name) DO UPDATE SET
            server_id       = excluded.server_id,
            mention_url     = excluded.mention_url,
            mention_context = excluded.mention_context,
            mention_status  = excluded.mention_status,
            last_seen       = now()
        """.strip(),
        params=[
            row_id,
            server_id,
            DIRECTORY_NAME,
            entry["registry_name"],
            mention_url,
            (entry["description"] or "")[:500],
            entry.get("status"),
        ],
    )


def upsert_candidate(entry: dict) -> None:
    row_id = _deterministic_id(DIRECTORY_NAME, entry["registry_name"])
    candidate_url = f"{REGISTRY_BASE}/v0/servers/{entry['registry_name']}"
    ws_execute(
        """
        INSERT INTO mcp_discovery_candidates
            (id, candidate_name, candidate_url, candidate_description,
             discovered_in_directory, discovered_status, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (discovered_in_directory, candidate_name) DO UPDATE SET
            candidate_url          = excluded.candidate_url,
            candidate_description  = excluded.candidate_description,
            discovered_status      = excluded.discovered_status,
            last_seen              = now()
        """.strip(),
        params=[
            row_id,
            entry["registry_name"],
            candidate_url,
            (entry["description"] or "")[:500],
            DIRECTORY_NAME,
            entry.get("status"),
        ],
    )


def upsert_facts(entry: dict, server_id: Optional[str]) -> None:
    row_id = _deterministic_id("facts", entry["registry_name"])
    ws_execute(
        """
        INSERT INTO mcp_registry_facts
            (id, registry_name, version, description, status, published_at,
             is_latest, package_count, primary_registry, primary_identifier,
             raw_packages, server_id, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (registry_name) DO UPDATE SET
            version            = excluded.version,
            description        = excluded.description,
            status             = excluded.status,
            published_at       = excluded.published_at,
            is_latest          = excluded.is_latest,
            package_count      = excluded.package_count,
            primary_registry   = excluded.primary_registry,
            primary_identifier = excluded.primary_identifier,
            raw_packages       = excluded.raw_packages,
            server_id          = excluded.server_id,
            last_seen          = now()
        """.strip(),
        params=[
            row_id,
            entry["registry_name"],
            entry.get("version"),
            (entry["description"] or "")[:500],
            entry.get("status"),
            entry.get("published_at"),
            entry.get("is_latest"),
            entry.get("package_count"),
            entry.get("primary_registry"),
            entry.get("primary_identifier"),
            json.dumps(entry.get("packages") or [])[:4000],
            server_id,
        ],
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(smoke: bool = False, hard_limit: Optional[int] = None) -> int:
    started = time.time()
    LOG.info(
        "starting registry ingestion (smoke=%s hard_limit=%s)", smoke, hard_limit
    )
    heartbeat()

    if not smoke:
        try:
            ensure_schema()
        except Exception as e:
            LOG.error("schema bootstrap failed: %s", e)
            return EXIT_WRITE_FAIL

    cursor = None
    page_num = 0
    total_seen = 0
    matched = 0
    candidates_added = 0
    write_errors = 0

    while True:
        page_num += 1
        if page_num > MAX_PAGES:
            LOG.warning(
                "hit MAX_PAGES=%d safety ceiling, stopping", MAX_PAGES
            )
            break

        # Fetch
        try:
            data = fetch_page(cursor=cursor)
        except Exception as e:
            LOG.error("fetch page %d failed: %s", page_num, e)
            return EXIT_FETCH_FAIL

        servers = data.get("servers") or []
        metadata = data.get("metadata") or {}
        next_cursor = metadata.get("nextCursor") or None
        LOG.info(
            "page %d: %d servers (cursor_next=%s)",
            page_num, len(servers), "yes" if next_cursor else "no",
        )

        if smoke and page_num == 1:
            # Print a small sample to stdout so the operator can eyeball
            # the API shape before trusting the parser.
            sample = servers[:3]
            print("\n=== SMOKE: first 3 entries as seen by parser ===")
            for raw in sample:
                e = extract_entry(raw)
                print(json.dumps(e, indent=2, default=str)[:1200])
                print("---")
            print("=== END SMOKE ===\n")
            return EXIT_OK

        # Process + write
        for raw in servers:
            entry = extract_entry(raw)
            if not entry:
                continue
            total_seen += 1

            try:
                registry_row = match_to_registry(entry)
            except Exception as e:
                LOG.warning("match failed for %r: %s", entry["registry_name"], e)
                registry_row = None

            server_id = registry_row["server_id"] if registry_row else None

            try:
                # Always write facts (rich metadata) whether or not we match
                upsert_facts(entry, server_id)
                if registry_row:
                    upsert_mention(entry, server_id)
                    matched += 1
                else:
                    upsert_candidate(entry)
                    candidates_added += 1
            except Exception as e:
                LOG.error(
                    "write failed for %r: %s", entry["registry_name"], e
                )
                write_errors += 1

            if hard_limit and total_seen >= hard_limit:
                LOG.info("hit hard_limit=%d, stopping", hard_limit)
                next_cursor = None
                break

        # Periodic heartbeat during long crawls
        if page_num % 5 == 0:
            heartbeat()

        if not next_cursor:
            break
        cursor = next_cursor
        time.sleep(INTER_PAGE_SLEEP_SEC)

    heartbeat()
    elapsed = time.time() - started
    LOG.info(
        "done: pages=%d seen=%d matched=%d candidates=%d write_errors=%d elapsed=%.1fs",
        page_num, total_seen, matched, candidates_added, write_errors, elapsed,
    )

    if write_errors and not (matched or candidates_added):
        return EXIT_WRITE_FAIL
    return EXIT_OK


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true",
                   help="Fetch one page, print sample entries, no writes.")
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after N total servers processed.")
    args = p.parse_args()
    return run(smoke=args.smoke, hard_limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())