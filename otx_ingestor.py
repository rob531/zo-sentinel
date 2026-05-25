#!/usr/bin/env python3
"""
otx_ingestor.py

Ingests AlienVault OTX (Open Threat Exchange) subscribed pulses into ZOMesh's
Sentinel trust stack. Two write destinations:

  1. threat_feed_cache   : raw IOC cache (hostnames / IPs / hashes)
                            feed_name='alienvault_otx'
                            Used for future O(1) lookups (check_indicator).

  2. mcp_threat_associations : specific findings where an OTX IOC matches
                                something in mcp_server_registry or
                                mcp_registry_facts. source='alienvault_otx'
                                Used for the risk register entries.

Scope boundary (important, enforced by code structure):
  This ingestor cross-references OTX findings ONLY against Sentinel-internal
  tables (mcp_server_registry.url domains, mcp_registry_facts.primary_identifier).
  It does NOT touch any firm/operational data. If you ever want OTX intel for
  non-MCP purposes, write a separate tool with its own scope. DO NOT extend
  this one.

Auth:
  Reads OTX key from os.environ['Alienvaultapi']. Never logged, never stored
  in DB, never echoed. If missing, the script aborts cleanly.

Rate limits:
  OTX free tier: ~60 requests/minute. We paginate subscribed pulses at 50
  per page with a 1-second inter-page sleep. A full subscribed-pulse pull
  for a normal subscription is O(10-200) pages = under 5 minutes.

Idempotency:
  Deterministic MD5 IDs on (source, identifier) for threat_feed_cache
  and (server_id, pulse_id, indicator) for mcp_threat_associations. Safe
  to re-run; second run UPSERTs and refreshes last_seen.

Design notes:
  * Uses /execute with parameterized SQL everywhere — no f-string SQL.
    (The existing threat_feed_cache.py has f-string SQL in upsert_indicators
    and check_indicator; that's a SQL injection vector on adversarial
    hostnames. Not this ingestor's job to fix it, but noted in session doc.)
  * Severity mapping follows OTX adversary/malware tags:
      - pulses tagged 'malware', 'ransomware', 'apt', 'c2'      → 'critical'
      - pulses tagged 'phishing', 'botnet', 'exploit'           → 'high'
      - pulses tagged 'suspicious', 'reconnaissance'            → 'medium'
      - everything else                                         → 'low'
  * Matching is strict: exact hostname match against mcp_server_registry
    URL's domain component. No fuzzy matches — false positives in threat
    context are worse than misses.

Flags:
  --smoke            Fetch ONE page, print pulse summaries + IOC counts, no writes.
  --max-pulses N     Stop after N pulses processed (default: unlimited within safety ceiling).
  --no-match         Skip mcp_threat_associations cross-referencing (feed-only mode).
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

LOG = logging.getLogger("otx_ingestor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

# Config
OTX_BASE = "https://otx.alienvault.com"
SUBSCRIBED_ENDPOINT = "/api/v1/pulses/subscribed"
PAGE_LIMIT = 50
MAX_PAGES = 100                # safety ceiling: 100 * 50 = 5000 pulses max
INTER_PAGE_SLEEP_SEC = 1.0     # respect OTX 60/min rate limit
FETCH_TIMEOUT_SEC = 30
EXECUTE_TIMEOUT_SEC = 15
WRITE_SERVICE_BASE = "http://127.0.0.1:8772"
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
FEED_NAME = "alienvault_otx"
SOURCE_NAME = "alienvault_otx"

EXIT_OK = 0
EXIT_AUTH_FAIL = 1
EXIT_FETCH_FAIL = 2
EXIT_WRITE_FAIL = 3

# Severity inference from pulse tags
SEVERITY_RULES = [
    ("critical", {"malware", "ransomware", "apt", "c2", "command-and-control",
                  "backdoor", "trojan", "stealer", "rootkit"}),
    ("high",     {"phishing", "botnet", "exploit", "rce", "vulnerability",
                  "cve", "supply-chain", "typosquat"}),
    ("medium",   {"suspicious", "reconnaissance", "scanner", "proxy",
                  "anonymizer", "abuse"}),
]
DEFAULT_SEVERITY = "low"

# Indicator types OTX uses
# See: https://otx.alienvault.com/api — IndicatorType enum
HOST_INDICATOR_TYPES = {"domain", "hostname"}
IP_INDICATOR_TYPES = {"IPv4", "IPv6"}
HASH_INDICATOR_TYPES = {"FileHash-MD5", "FileHash-SHA1", "FileHash-SHA256"}
URL_INDICATOR_TYPES = {"URL"}
PACKAGE_INDICATOR_TYPES = {"CVE"}   # OTX includes CVE indicators sometimes


# ---------------------------------------------------------------------------
# Auth + WriteService helpers
# ---------------------------------------------------------------------------

def get_api_key() -> Optional[str]:
    """Read OTX API key from environment. Never logs the value."""
    key = os.environ.get("Alienvaultapi") or os.environ.get("ALIENVAULTAPI")
    if not key:
        return None
    # Minimal sanity check without logging the value
    if len(key) < 20:
        LOG.error("Alienvaultapi secret present but looks malformed (too short)")
        return None
    return key


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
                    "service": "otx_ingestor",
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception as e:
        LOG.warning("heartbeat failed: %s", e)


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent)
# ---------------------------------------------------------------------------

SCHEMA_THREAT_FEED_CACHE = """
CREATE TABLE IF NOT EXISTS threat_feed_cache (
    id               BIGINT PRIMARY KEY,
    feed_name        VARCHAR NOT NULL,
    indicator        VARCHAR NOT NULL,
    indicator_type   VARCHAR,
    pulse_id         VARCHAR,
    pulse_name       VARCHAR,
    severity         VARCHAR,
    last_refreshed   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (feed_name, indicator)
)
""".strip()

# mcp_threat_associations already exists — don't redefine, just use.


def ensure_schema() -> None:
    ws_execute(SCHEMA_THREAT_FEED_CACHE)


# ---------------------------------------------------------------------------
# OTX fetch
# ---------------------------------------------------------------------------

def fetch_pulses_page(api_key: str, page: int) -> dict:
    """Fetch one page of subscribed pulses. Never echoes the key."""
    r = requests.get(
        OTX_BASE + SUBSCRIBED_ENDPOINT,
        params={"limit": PAGE_LIMIT, "page": page},
        headers={
            "X-OTX-API-KEY": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=FETCH_TIMEOUT_SEC,
    )
    if r.status_code == 403:
        LOG.error(
            "OTX returned 403 Forbidden — API key may be invalid or expired. "
            "Verify the 'Alienvaultapi' secret is correct."
        )
        raise SystemExit(EXIT_AUTH_FAIL)
    r.raise_for_status()
    return r.json()


def infer_severity(tags: list) -> str:
    """Map pulse tags to a severity tier. Lowercases and handles missing tags."""
    if not tags:
        return DEFAULT_SEVERITY
    tag_set = {str(t).lower() for t in tags}
    for severity, triggers in SEVERITY_RULES:
        if tag_set & triggers:
            return severity
    return DEFAULT_SEVERITY


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _det_id(*parts: str) -> int:
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest()[:8], 16) % (2**31)


def upsert_feed_cache(
    indicator: str,
    indicator_type: str,
    pulse_id: str,
    pulse_name: str,
    severity: str,
) -> None:
    row_id = _det_id(FEED_NAME, indicator)
    ws_execute(
        """
        INSERT INTO threat_feed_cache
            (id, feed_name, indicator, indicator_type,
             pulse_id, pulse_name, severity, last_refreshed)
        VALUES (?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (feed_name, indicator) DO UPDATE SET
            indicator_type  = excluded.indicator_type,
            pulse_id        = excluded.pulse_id,
            pulse_name      = excluded.pulse_name,
            severity        = excluded.severity,
            last_refreshed  = now()
        """.strip(),
        params=[
            row_id,
            FEED_NAME,
            indicator,
            indicator_type,
            pulse_id,
            (pulse_name or "")[:200],
            severity,
        ],
    )


def upsert_threat_association(
    server_id: str,
    threat_type: str,
    severity: str,
    evidence: str,
    pulse_id: str,
    indicator: str,
) -> None:
    # mcp_threat_associations has no UNIQUE constraint we can rely on,
    # so we use a deterministic id and INSERT OR REPLACE semantics via
    # DELETE-then-INSERT on the stable id.
    row_id = _det_id(server_id, pulse_id, indicator)
    ws_execute(
        "DELETE FROM mcp_threat_associations WHERE id = ?",
        params=[row_id],
    )
    ws_execute(
        """
        INSERT INTO mcp_threat_associations
            (id, server_id, threat_type, severity, evidence, source, reported_at)
        VALUES (?, ?, ?, ?, ?, ?, now())
        """.strip(),
        params=[
            row_id,
            server_id,
            threat_type,
            severity,
            (evidence or "")[:500],
            SOURCE_NAME,
        ],
    )


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------

def _url_host(url: str) -> str:
    """Extract lowercase hostname from a URL, empty string if unparseable."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def match_indicators_to_registry(
    host_indicators: dict,
) -> list:
    """
    Given a dict of {hostname: (pulse_id, pulse_name, severity)} from OTX,
    find mcp_server_registry rows whose URL hostname matches, and return
    a list of association tuples ready for insert.

    We pull ALL registry URLs once, hash their hostnames, and intersect
    in Python rather than issuing N queries. More efficient at scale.
    """
    rows = ws_query(
        "SELECT server_id, name, url FROM mcp_server_registry "
        "WHERE url IS NOT NULL AND url != ''"
    )
    host_to_servers = {}
    for r in rows:
        h = _url_host(r.get("url", ""))
        if h:
            host_to_servers.setdefault(h, []).append(r)

    associations = []
    for indicator, (pulse_id, pulse_name, severity) in host_indicators.items():
        matched = host_to_servers.get(indicator.lower())
        if not matched:
            continue
        for reg_row in matched:
            associations.append({
                "server_id": reg_row["server_id"],
                "server_name": reg_row["name"],
                "threat_type": "infrastructure_exposure",
                "severity": severity,
                "evidence": (
                    f"OTX pulse '{pulse_name}' (id={pulse_id}) "
                    f"lists indicator '{indicator}' which matches "
                    f"registered URL host for '{reg_row['name']}'"
                ),
                "pulse_id": pulse_id,
                "indicator": indicator,
            })
    return associations


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(smoke: bool = False, max_pulses: Optional[int] = None,
       skip_match: bool = False) -> int:
    started = time.time()
    LOG.info("starting OTX ingestion (smoke=%s max_pulses=%s skip_match=%s)",
             smoke, max_pulses, skip_match)

    api_key = get_api_key()
    if not api_key:
        LOG.error("OTX API key not found in env var 'Alienvaultapi'. Aborting.")
        return EXIT_AUTH_FAIL
    LOG.info("OTX API key loaded from env (length=%d)", len(api_key))

    heartbeat()

    if not smoke:
        try:
            ensure_schema()
        except Exception as e:
            LOG.error("schema bootstrap failed: %s", e)
            return EXIT_WRITE_FAIL

    # Aggregate across pages
    total_pulses = 0
    total_indicators = 0
    host_indicator_map = {}  # {hostname: (pulse_id, pulse_name, severity)} for matching
    write_errors = 0

    page = 1
    while page <= MAX_PAGES:
        try:
            data = fetch_pulses_page(api_key, page)
        except SystemExit:
            return EXIT_AUTH_FAIL
        except Exception as e:
            LOG.error("fetch page %d failed: %s", page, e)
            return EXIT_FETCH_FAIL

        results = data.get("results") or []
        next_page = data.get("next")
        LOG.info("page %d: %d pulses (next=%s)",
                 page, len(results), "yes" if next_page else "no")

        if smoke and page == 1:
            print("\n=== SMOKE: first 3 pulses summarized ===")
            for p in results[:3]:
                pulse_name = p.get("name", "<unnamed>")
                pulse_id = p.get("id", "<no-id>")
                tags = p.get("tags") or []
                indicators = p.get("indicators") or []
                sev = infer_severity(tags)
                print(f"  pulse: {pulse_name}")
                print(f"    id: {pulse_id}")
                print(f"    tags: {tags[:10]}")
                print(f"    severity_inferred: {sev}")
                print(f"    indicators: {len(indicators)}")
                if indicators:
                    sample_types = {}
                    for ind in indicators[:20]:
                        t = ind.get("type", "?")
                        sample_types[t] = sample_types.get(t, 0) + 1
                    print(f"    indicator_types (sample): {sample_types}")
                    print(f"    first indicator: {indicators[0].get('indicator', '')[:80]}")
                print("  ---")
            print("=== END SMOKE ===\n")
            return EXIT_OK

        for pulse in results:
            total_pulses += 1
            pulse_id = str(pulse.get("id") or "")
            pulse_name = pulse.get("name") or ""
            tags = pulse.get("tags") or []
            severity = infer_severity(tags)
            indicators = pulse.get("indicators") or []

            for ind in indicators:
                ind_type = ind.get("type", "")
                ind_value = (ind.get("indicator") or "").strip()
                if not ind_value:
                    continue

                # Write to feed cache for all indicator types we recognize
                try:
                    upsert_feed_cache(
                        indicator=ind_value,
                        indicator_type=ind_type,
                        pulse_id=pulse_id,
                        pulse_name=pulse_name,
                        severity=severity,
                    )
                    total_indicators += 1
                except Exception as e:
                    LOG.warning("feed_cache upsert failed (%s=%s): %s",
                                ind_type, ind_value[:60], e)
                    write_errors += 1

                # Collect host-type indicators for cross-ref phase
                if ind_type in HOST_INDICATOR_TYPES:
                    host_indicator_map[ind_value.lower()] = (
                        pulse_id, pulse_name, severity
                    )

            if max_pulses and total_pulses >= max_pulses:
                LOG.info("reached max_pulses=%d, stopping", max_pulses)
                next_page = None
                break

        if page % 5 == 0:
            heartbeat()

        if not next_page:
            break
        page += 1
        time.sleep(INTER_PAGE_SLEEP_SEC)

    if page > MAX_PAGES:
        LOG.warning("hit MAX_PAGES=%d safety ceiling", MAX_PAGES)

    # Cross-reference phase
    matches_written = 0
    if not skip_match and host_indicator_map:
        LOG.info("cross-referencing %d host indicators against mcp_server_registry...",
                 len(host_indicator_map))
        try:
            associations = match_indicators_to_registry(host_indicator_map)
            LOG.info("found %d MCP-threat associations", len(associations))
            for a in associations:
                try:
                    upsert_threat_association(
                        server_id=a["server_id"],
                        threat_type=a["threat_type"],
                        severity=a["severity"],
                        evidence=a["evidence"],
                        pulse_id=a["pulse_id"],
                        indicator=a["indicator"],
                    )
                    matches_written += 1
                    LOG.info("ASSOCIATION: %s [%s] ← %s",
                             a["server_name"], a["severity"], a["indicator"])
                except Exception as e:
                    LOG.error("failed to write association for %s: %s",
                              a["server_name"], e)
                    write_errors += 1
        except Exception as e:
            LOG.error("cross-reference phase failed: %s", e)
            # Don't return — the feed_cache writes are still valid

    heartbeat()
    elapsed = time.time() - started
    LOG.info(
        "done: pulses=%d indicators=%d host_indicators=%d matches=%d "
        "write_errors=%d elapsed=%.1fs",
        total_pulses, total_indicators, len(host_indicator_map),
        matches_written, write_errors, elapsed,
    )

    if write_errors and total_indicators == 0:
        return EXIT_WRITE_FAIL
    return EXIT_OK


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true",
                   help="Fetch one page, print sample pulses, no writes.")
    p.add_argument("--max-pulses", type=int, default=None,
                   help="Stop after N pulses processed.")
    p.add_argument("--no-match", action="store_true",
                   help="Skip the MCP cross-reference phase (feed-only mode).")
    args = p.parse_args()
    return run(smoke=args.smoke, max_pulses=args.max_pulses,
               skip_match=args.no_match)


if __name__ == "__main__":
    sys.exit(main())