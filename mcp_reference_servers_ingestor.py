#!/usr/bin/env python3
"""
mcp_reference_servers_ingestor.py

Ingests Anthropic's curated MCP reference server list from the canonical
GitHub README. This list is deprecated upstream (Anthropic has pointed
users to the MCP Registry going forward) but the servers it names are
explicitly Anthropic-endorsed — a strong positive trust signal worth
preserving regardless of whether the list itself keeps being maintained.

Source: https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md

Produces two sets of rows:

  • mcp_directory_mentions  : entries that match something in mcp_server_registry.
                                These carry the 'anthropic_reference' directory name
                                and a status of either 'active' (current reference
                                server) or 'archived' (deprecated reference server).

  • mcp_discovery_candidates : entries Anthropic endorses that are NOT in our
                                current registry. These are worth review — e.g.
                                Python reference servers we haven't caught yet.

One-shot design. Run daily via wrapper. Writes are idempotent via
deterministic MD5-derived IDs, so a second invocation UPSERTs.

Design decisions:
  - Uses /execute endpoint (not /write) because these are new tables
    with custom PK structure — /write auto-injects an id column which
    would fight our schema. (Learned the hard way on 2026-04-19 with
    the ecosystems fetcher.)
  - CREATE TABLE IF NOT EXISTS is idempotent and runs every invocation
    so the tables come into existence on first run without ceremony.
  - Matching uses explicit IN-placeholder expansion rather than
    array-valued parameters to avoid driver-marshalling ambiguity.
  - Bullet regex matches DASH bullets (`-`), not asterisk bullets.
    The raw README uses `- **[Name](url)** - Description`.
    My first attempt used `^\*` based on the GitHub HTML render, which
    was visually misleading. Verified against raw markdown 2026-04-20.
"""

import hashlib
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

LOG = logging.getLogger("mcp_reference_servers_ingestor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

README_URL = (
    "https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md"
)
WRITE_SERVICE_BASE = "http://127.0.0.1:8772"
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
FROM_HEADER = "hello@zocomputer.io"
FETCH_TIMEOUT_SEC = 15
EXECUTE_TIMEOUT_SEC = 10
DIRECTORY_NAME = "anthropic_reference"

# Exit codes — match commit-3 conventions
EXIT_OK = 0
EXIT_FETCH_FAIL = 1
EXIT_PARSE_FAIL = 2
EXIT_WRITE_FAIL = 3


# ---------------------------------------------------------------------------
# Schema bootstrap
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
    """Best-effort heartbeat. Failures are logged but non-fatal."""
    try:
        requests.post(
            WRITE_SERVICE_BASE + "/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "mcp_reference_servers_ingestor",
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


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_readme() -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "From": FROM_HEADER,
        "Accept": "text/plain, text/markdown",
    }
    r = requests.get(README_URL, headers=headers, timeout=FETCH_TIMEOUT_SEC)
    r.raise_for_status()
    return r.text


# Section headers in the README that bound the lists we care about.
# The README uses '## 🌟 Reference Servers' (H2) with '### Archived' (H3) nested.
# We stop at the next major section (next H2) for the outer bounds.
_REFERENCE_SECTION_RE = re.compile(
    r"^##\s+.*?Reference Servers\s*$", re.MULTILINE | re.IGNORECASE
)
_ARCHIVED_SECTION_RE = re.compile(
    r"^###\s+Archived\s*$", re.MULTILINE | re.IGNORECASE
)
_NEXT_MAJOR_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
_NEXT_SECTION_RE = re.compile(r"^##?#?\s+", re.MULTILINE)

# Bullet format in the RAW README (verified against fetched markdown):
#   - **[Filesystem](/modelcontextprotocol/servers/blob/main/src/filesystem)** - Secure file operations with configurable access controls.
#   - **[Brave Search](https://github.com/modelcontextprotocol/servers-archived/tree/main/src/brave-search)** - Web and local search using Brave's Search API.
#
# Key points:
#   * Bullet marker is literal '-' (hyphen), not '*'.
#   * Link text is wrapped in **bold** markdown.
#   * Separator between the closing '**' and description is ' - ' (plain hyphen).
_BULLET_RE = re.compile(
    r"^-\s+\*\*\[(?P<n>[^\]]+)\]\((?P<url>[^)]+)\)\*\*\s*[—–-]\s*(?P<desc>.+?)\s*$",
    re.MULTILINE,
)


def _extract_section(md: str, start_re: re.Pattern, end_re: re.Pattern) -> str:
    start_match = start_re.search(md)
    if not start_match:
        return ""
    start = start_match.end()
    end_match = end_re.search(md, pos=start)
    end = end_match.start() if end_match else len(md)
    return md[start:end]


def parse_reference_section(md: str) -> list:
    """
    Returns entries from the 'Reference Servers' section, EXCLUDING the
    nested '### Archived' subsection (parsed separately).

    Each entry: {name, url, description, status='active'}
    """
    section = _extract_section(md, _REFERENCE_SECTION_RE, _NEXT_MAJOR_SECTION_RE)
    archived_match = _ARCHIVED_SECTION_RE.search(section)
    if archived_match:
        section = section[: archived_match.start()]
    return [
        {
            "name": m.group("n").strip(),
            "url": m.group("url").strip(),
            "description": m.group("desc").strip(),
            "status": "active",
        }
        for m in _BULLET_RE.finditer(section)
    ]


def parse_archived_section(md: str) -> list:
    """
    Returns entries from the '### Archived' subsection within
    'Reference Servers'. Each entry: {name, url, description, status='archived'}
    """
    archived_match = _ARCHIVED_SECTION_RE.search(md)
    if not archived_match:
        return []
    start = archived_match.end()
    end_match = _NEXT_SECTION_RE.search(md, pos=start)
    end = end_match.start() if end_match else len(md)
    section = md[start:end]
    return [
        {
            "name": m.group("n").strip(),
            "url": m.group("url").strip(),
            "description": m.group("desc").strip(),
            "status": "archived",
        }
        for m in _BULLET_RE.finditer(section)
    ]


# ---------------------------------------------------------------------------
# Match to registry
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    if not name:
        return ""
    return name.strip().lower()


def _candidate_npm_names(display_name: str) -> list:
    """
    Build plausible npm package names from a README display name.

    'Filesystem' → ['@modelcontextprotocol/server-filesystem', 'mcp-server-filesystem', 'filesystem']
    'Sequential Thinking' → ['@modelcontextprotocol/server-sequential-thinking', ...]
    'AWS KB Retrieval' → ['@modelcontextprotocol/server-aws-kb-retrieval', ...]
    """
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    return [
        f"@modelcontextprotocol/server-{slug}",
        f"mcp-server-{slug}",
        slug,
    ]


def _extract_repo_path(url: str) -> str:
    """
    For a GitHub URL, extract the owner/repo[/path] component.
    Returns empty string for non-GitHub URLs or unparseable input.
    """
    if not url or "github.com" not in url:
        return ""
    m = re.match(r"https?://(?:www\.)?github\.com/([^?#]+)", url)
    if not m:
        return ""
    return m.group(1).rstrip("/").rstrip(".git").lower()


def match_to_registry(entry: dict) -> Optional[dict]:
    """
    Try to match a README entry to a row in mcp_server_registry.

    Strategy order:
      1. Exact match on any plausible npm name (built from the display name).
      2. Exact match on the display name itself.
      3. Substring match on stored url containing the README's repo path.

    Returns the matched registry row (dict) or None.
    """
    # Strategy 1: constructed npm-style names. Expand into an explicit
    # IN-list of placeholders (safer than array params across drivers).
    candidates = [c.lower() for c in _candidate_npm_names(entry["name"])]
    if candidates:
        placeholders = ", ".join(["?"] * len(candidates))
        rows = ws_query(
            f"SELECT server_id, name, url FROM mcp_server_registry "
            f"WHERE LOWER(name) IN ({placeholders}) LIMIT 1",
            params=candidates,
        )
        if rows:
            return rows[0]

    # Strategy 2: exact-ish match on display name (rarely hits but cheap)
    display = _normalize_name(entry["name"])
    if display:
        rows = ws_query(
            "SELECT server_id, name, url FROM mcp_server_registry "
            "WHERE LOWER(name) = ? LIMIT 1",
            params=[display],
        )
        if rows:
            return rows[0]

    # Strategy 3: repo-path substring on stored url
    repo_path = _extract_repo_path(entry.get("url", ""))
    if repo_path:
        rows = ws_query(
            "SELECT server_id, name, url FROM mcp_server_registry "
            "WHERE LOWER(url) LIKE ? LIMIT 1",
            params=[f"%{repo_path}%"],
        )
        if rows:
            return rows[0]

    return None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _deterministic_id(*parts: str) -> int:
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    # 31 bits keeps us inside any INTEGER/BIGINT signed range
    return int(hashlib.md5(raw).hexdigest()[:8], 16) % (2**31)


def upsert_mention(
    server_id: str,
    mention_name: str,
    mention_url: str,
    mention_context: str,
    mention_status: str,
) -> None:
    row_id = _deterministic_id(DIRECTORY_NAME, mention_name)
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
            mention_name,
            mention_url,
            mention_context[:500] if mention_context else None,
            mention_status,
        ],
    )


def upsert_candidate(
    candidate_name: str,
    candidate_url: str,
    candidate_description: str,
    candidate_status: str,
) -> None:
    row_id = _deterministic_id(DIRECTORY_NAME, candidate_name)
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
            candidate_name,
            candidate_url,
            candidate_description[:500] if candidate_description else None,
            DIRECTORY_NAME,
            candidate_status,
        ],
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run() -> int:
    started = time.time()
    LOG.info("starting anthropic reference servers ingestion")
    heartbeat()

    # 1. Fetch
    try:
        md = fetch_readme()
        LOG.info("fetched README (%d bytes)", len(md))
    except Exception as e:
        LOG.error("fetch failed: %s", e)
        return EXIT_FETCH_FAIL

    # 2. Parse
    try:
        active = parse_reference_section(md)
        archived = parse_archived_section(md)
    except Exception as e:
        LOG.error("parse crashed: %s", e)
        return EXIT_PARSE_FAIL

    all_entries = active + archived
    LOG.info(
        "parsed: active=%d archived=%d total=%d",
        len(active), len(archived), len(all_entries),
    )
    if not all_entries:
        LOG.warning(
            "no entries parsed — README format may have changed, aborting write"
        )
        return EXIT_PARSE_FAIL

    # 3. Schema (only after we know we have content worth writing)
    try:
        ensure_schema()
    except Exception as e:
        LOG.error("schema bootstrap failed: %s", e)
        return EXIT_WRITE_FAIL

    # 4. Match + write
    matched_count = 0
    candidate_count = 0
    write_errors = 0

    for entry in all_entries:
        try:
            registry_row = match_to_registry(entry)
        except Exception as e:
            LOG.warning("match failed for %r: %s", entry["name"], e)
            registry_row = None

        try:
            if registry_row:
                upsert_mention(
                    server_id=registry_row["server_id"],
                    mention_name=entry["name"],
                    mention_url=entry["url"],
                    mention_context=entry["description"],
                    mention_status=entry["status"],
                )
                matched_count += 1
                LOG.info(
                    "matched: %s -> %s (%s)",
                    entry["name"], registry_row["name"], entry["status"],
                )
            else:
                upsert_candidate(
                    candidate_name=entry["name"],
                    candidate_url=entry["url"],
                    candidate_description=entry["description"],
                    candidate_status=entry["status"],
                )
                candidate_count += 1
                LOG.info(
                    "candidate: %s (%s) -- not in registry",
                    entry["name"], entry["status"],
                )
        except Exception as e:
            LOG.error("write failed for %r: %s", entry["name"], e)
            write_errors += 1

    heartbeat()
    elapsed = time.time() - started
    LOG.info(
        "done: parsed=%d matched=%d candidates=%d write_errors=%d elapsed=%.2fs",
        len(all_entries), matched_count, candidate_count, write_errors, elapsed,
    )

    if write_errors and not (matched_count or candidate_count):
        return EXIT_WRITE_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())