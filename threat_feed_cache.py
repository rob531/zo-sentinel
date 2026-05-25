#!/usr/bin/env python3
"""
threat_feed_cache.py -- Commit A

One-shot cache refresher for public threat intelligence feeds.
Maintains local cache of malicious indicators from URLhaus, OpenPhish,
and PhishTank. Designed to be invoked daily by a daemon wrapper.

One-shot execution: __main__ calls refresh_all_feeds() and exits.
No polling loop. Designed for daily cron/daemon invocation.

Storage: Only hostnames stored (not full URLs). PII-free by design.
"""
import hashlib
import json
import logging
import socket
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

SERVICE_NAME = "threat_feed_cache"
WRITE_SERVICE = "http://127.0.0.1:8772"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"
QUERY_URL = f"{WRITE_SERVICE}/query"

USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"
FROM_HEADER = "hello@zocomputer.io"
FETCH_TIMEOUT_S = 30
REQUEST_DELAY_S = 0.5

FEEDS = {
    "urlhaus": {
        "url": "https://urlhaus.abuse.ch/downloads/csv_recent/",
        "format": "csv",
    },
    "openphish": {
        "url": "https://openphish.com/feed.txt",
        "format": "text",
    },
    "phishtank": {
        "url": "https://data.phishtank.com/data/online-valid.json",
        "format": "json",
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [threat_feed_cache] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(SERVICE_NAME)


def ws_execute(sql: str) -> dict:
    """Execute SQL via WriteService execute endpoint."""
    resp = requests.post(
        EXECUTE_URL,
        json={"sql": sql},
        headers={"Content-Type": "application/json"},
        timeout=FETCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> dict:
    """Query data via WriteService query endpoint."""
    resp = requests.post(
        QUERY_URL,
        json={"sql": sql},
        headers={"Content-Type": "application/json"},
        timeout=FETCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()


def ensure_table() -> None:
    """Create threat_feed_cache table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS threat_feed_cache (
        id BIGINT PRIMARY KEY,
        feed_name VARCHAR NOT NULL,
        indicator VARCHAR NOT NULL,
        indicator_type VARCHAR DEFAULT 'domain',
        first_seen_in_feed TIMESTAMPTZ DEFAULT now(),
        last_refreshed TIMESTAMPTZ DEFAULT now(),
        UNIQUE (feed_name, indicator)
    )
    """
    ws_execute(sql)
    log.info("Table threat_feed_cache ensured")


def compute_id(feed_name: str, indicator: str) -> int:
    """Compute deterministic BIGINT ID from feed_name:indicator."""
    key = f"{feed_name}:{indicator}".encode("utf-8")
    hash_hex = hashlib.md5(key).hexdigest()[:8]
    return int(hash_hex, 16) % (2**31)


def extract_hostname(url: str) -> str | None:
    """Extract lowercase hostname from URL, or None if invalid."""
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return None
        hostname = parsed.hostname.lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return None


def fetch_urlhaus(csv_url: str) -> list[str]:
    """Fetch URLhaus CSV and extract hostnames."""
    hostnames = []
    try:
        log.info("Fetching URLhaus feed...")
        start = time.time()
        resp = requests.get(
            csv_url,
            headers={
                "User-Agent": USER_AGENT,
                "From": FROM_HEADER,
            },
            timeout=FETCH_TIMEOUT_S,
        )
        elapsed = time.time() - start
        log.info(f"URLhaus HTTP status: {resp.status_code}, elapsed: {elapsed:.1f}s")
        resp.raise_for_status()

        lines = resp.text.strip().split("\n")
        log.info(f"URLhaus lines received: {len(lines)}")

        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            url = parts[2].strip().strip('"')
            if not url:
                continue
            hostname = extract_hostname(url)
            if hostname:
                hostnames.append(hostname)

        log.info(f"URLhaus hostnames parsed: {len(hostnames)}")

    except requests.RequestException as e:
        log.error(f"URLhaus fetch failed: {e}")
    except Exception as e:
        log.error(f"URLhaus parse error: {e}")

    return hostnames


def fetch_openphish(feed_url: str) -> list[str]:
    """Fetch OpenPhish plain text feed and extract hostnames."""
    hostnames = []
    try:
        log.info("Fetching OpenPhish feed...")
        start = time.time()
        resp = requests.get(
            feed_url,
            headers={
                "User-Agent": USER_AGENT,
                "From": FROM_HEADER,
            },
            timeout=FETCH_TIMEOUT_S,
        )
        elapsed = time.time() - start
        log.info(f"OpenPhish HTTP status: {resp.status_code}, elapsed: {elapsed:.1f}s")
        resp.raise_for_status()

        lines = [l.strip() for l in resp.text.strip().split("\n") if l.strip()]
        log.info(f"OpenPhish lines received: {len(lines)}")

        for line in lines:
            if line.startswith("#"):
                continue
            hostname = extract_hostname(line)
            if hostname:
                hostnames.append(hostname)

        log.info(f"OpenPhish hostnames parsed: {len(hostnames)}")

    except requests.RequestException as e:
        log.error(f"OpenPhish fetch failed: {e}")
    except Exception as e:
        log.error(f"OpenPhish parse error: {e}")

    return hostnames


def fetch_phishtank(json_url: str) -> list[str]:
    """Fetch PhishTank JSON and extract hostnames."""
    hostnames = []
    try:
        log.info("Fetching PhishTank feed...")
        start = time.time()
        resp = requests.get(
            json_url,
            headers={
                "User-Agent": USER_AGENT,
                "From": FROM_HEADER,
            },
            timeout=FETCH_TIMEOUT_S,
        )
        elapsed = time.time() - start
        log.info(f"PhishTank HTTP status: {resp.status_code}, elapsed: {elapsed:.1f}s")
        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, list):
            log.info(f"PhishTank entries received: {len(data)}")
            for entry in data:
                if isinstance(entry, dict):
                    url = entry.get("url", "")
                    hostname = extract_hostname(url)
                    if hostname:
                        hostnames.append(hostname)
        else:
            log.warning(f"PhishTank unexpected data format: {type(data)}")

        log.info(f"PhishTank hostnames parsed: {len(hostnames)}")

    except requests.RequestException as e:
        log.error(f"PhishTank fetch failed: {e}")
    except json.JSONDecodeError as e:
        log.error(f"PhishTank JSON decode failed: {e}")
    except Exception as e:
        log.error(f"PhishTank parse error: {e}")

    return hostnames


def upsert_indicators(feed_name: str, hostnames: list[str]) -> int:
    """Upsert hostnames for a feed. Returns count of rows upserted."""
    if not hostnames:
        return 0

    upserted = 0
    unique_hostnames = list(set(hostnames))

    for hostname in unique_hostnames:
        try:
            rid = compute_id(feed_name, hostname)
            sql = f"""
            INSERT INTO threat_feed_cache (id, feed_name, indicator, indicator_type, last_refreshed)
            VALUES ({rid}, '{feed_name}', '{hostname}', 'domain', now())
            ON CONFLICT (feed_name, indicator) DO UPDATE SET
                last_refreshed = now()
            """
            ws_execute(sql)
            upserted += 1
        except Exception as e:
            log.warning(f"Failed to upsert {hostname}: {e}")

        time.sleep(REQUEST_DELAY_S)

    return upserted


def fetch_feed(feed_name: str) -> list[str]:
    """Dispatch fetch based on feed format."""
    feed_config = FEEDS[feed_name]
    fmt = feed_config["format"]
    url = feed_config["url"]

    if feed_name == "urlhaus":
        return fetch_urlhaus(url)
    elif feed_name == "openphish":
        return fetch_openphish(url)
    elif feed_name == "phishtank":
        return fetch_phishtank(url)
    else:
        log.warning(f"Unknown feed: {feed_name}")
        return []


def refresh_all_feeds() -> dict:
    """
    Refresh all threat feeds. Returns dict with per-feed counts.
    One-shot execution -- no loop, no sleep.
    """
    ensure_table()

    results = {}
    total_start = time.time()

    for feed_name in FEEDS:
        feed_start = time.time()
        log.info(f"Starting refresh for {feed_name}...")

        hostnames = fetch_feed(feed_name)
        if hostnames:
            upserted = upsert_indicators(feed_name, hostnames)
            results[feed_name] = upserted
            log.info(f"{feed_name}: {len(hostnames)} parsed, {upserted} upserted, {time.time()-feed_start:.1f}s elapsed")
        else:
            results[feed_name] = 0
            log.info(f"{feed_name}: no hostnames parsed, 0 upserted")

        time.sleep(REQUEST_DELAY_S)

    total_elapsed = time.time() - total_start
    log.info(f"refresh_all_feeds() complete. Total elapsed: {total_elapsed:.1f}s")
    log.info(f"Results: {results}")

    return results


def check_indicator(hostname: str) -> list[str]:
    """
    Check if hostname appears in any active feed.
    Returns list of feed_name strings where last_refreshed < 30 days.
    Fast lookup, <50ms expected.
    """
    try:
        sql = f"""
        SELECT feed_name FROM threat_feed_cache
        WHERE indicator = '{hostname}'
          AND last_refreshed > now() - INTERVAL '30 days'
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        return [row["feed_name"] for row in rows]
    except Exception as e:
        log.error(f"check_indicator query failed for {hostname}: {e}")
        return []


def run():
    """One-shot execution entry point."""
    log.info("=== threat_feed_cache run() started ===")
    try:
        results = refresh_all_feeds()
        print(json.dumps(results))
        log.info("=== threat_feed_cache run() complete ===")
    except Exception as e:
        log.error(f"Fatal error in run(): {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()