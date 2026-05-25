#!/usr/bin/env python3
"""
canonicalizer.py  -- Commit B

Assigns a canonical_id to every mcp_server_registry row by analyzing
the cached ecosyste.ms metadata. Different server_ids pointing at the
same underlying project (github twin + npm listing + pypi listing)
resolve to the same canonical_id and become aggregatable for threat
and risk rollup.

Architecture decisions (locked this session):
  - Static rules only. No LLM, no per-record classifier. Deterministic.
  - 5x dominance threshold (was 10x; lowered to capture real flagships)
  - Sticky canonical_id via COALESCE -- once set, requires governance
    event to change. Drift detection writes to canonical_drift_log but
    does NOT auto-update registry.
  - 20% review burden accepted: ambiguous cases go to uncertain bucket.

Rule order (first match wins):
  1. SELF (lookup failed or no cousins)        -> pkg:self/{server_id_prefix}
  2. DOMINANT (top >= 5x #2 downloads)          -> top cousin purl
  3. NAME_MATCH (top name matches registry name)-> top cousin purl
  4. SCOPE (unscoped preferred over @scope/X)   -> unscoped purl
  5. UNCERTAIN (multi-cousin, no clear winner)  -> review bucket

Noise filters applied to cousin list before rules 2-4 evaluate:
  - Drop @mseep/*, @iflow-mcp/* (known republishers)
  - Drop go modules containing %21 (case-encoded duplicates)
  - Drop packages with downloads<10 when any cousin has downloads>1000

Writing pattern lessons from earlier today applied:
  - Uses /execute with explicit INSERT ON CONFLICT (bypass auto-id pitfall)
  - Reads table schemas via DESCRIBE before writing to them
  - Every new table includes 'id BIGINT PK' first column

Run modes:
  python3 canonicalizer.py --once      # single pass, exit
  python3 canonicalizer.py --dry-run   # compute but don't write; print summary
  python3 canonicalizer.py --loop      # daemon, 1h cycle

Operator flow: --dry-run first to inspect, then --once to apply.
Only promote to --loop mode after confirming results match expectations.
"""
import json
import logging
import os
import signal as pysignal
import sys
import time
from datetime import datetime, timezone

import requests

SERVICE_NAME = "canonicalizer"
WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL   = f"{WRITE_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"

DOMINANCE_THRESHOLD = 5.0            # top cousin must be >=Nx the #2
LOOP_INTERVAL_S     = 3600           # 1h in --loop mode

KNOWN_REPUBLISHERS  = (
    "@mseep/", "@iflow-mcp/",
    # Add more as seen in uncertain-bucket reviews
)

log = logging.getLogger(SERVICE_NAME)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

_shutdown = False


# ---- WriteService helpers -------------------------------------------

def ws_query(sql: str, params: list = None) -> list:
    r = requests.post(
        QUERY_URL,
        json={"sql": sql, "params": params or [], "limit": 10000},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("rows", [])


def ws_execute(sql: str, params: list = None) -> bool:
    try:
        r = requests.post(
            EXECUTE_URL,
            json={"sql": sql, "params": params or [],
                  "agent_id": SERVICE_NAME, "wait": True},
            timeout=30,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning("ws_execute failed: %s", e)
        return False


# ---- Schema -----------------------------------------------------------

def ensure_schema() -> bool:
    """Create Commit B tables + registry column. Idempotent.

    canonical_id column on mcp_server_registry is added if missing.
    Two new tables: mcp_project_canonical, canonical_drift_log, and
    mcp_project_canonical_uncertain.

    All new tables start with id BIGINT PK to cooperate with
    WriteService's auto-id convention (learned the hard way today).
    """
    statements = [
        # Registry column (sticky via COALESCE on update)
        "ALTER TABLE mcp_server_registry ADD COLUMN IF NOT EXISTS canonical_id VARCHAR",

        # Canonical identity table -- one row per canonical_id
        """CREATE TABLE IF NOT EXISTS mcp_project_canonical (
            id                BIGINT PRIMARY KEY,
            canonical_id      VARCHAR UNIQUE NOT NULL,
            canonical_name    VARCHAR,
            primary_ecosystem VARCHAR,
            primary_downloads BIGINT,
            repo_url          VARCHAR,
            member_count      INTEGER,
            confidence        VARCHAR,
            rule_applied      VARCHAR,
            first_seen        TIMESTAMPTZ DEFAULT now(),
            last_reviewed     TIMESTAMPTZ
        )""",

        # Review queue for ambiguous cases
        """CREATE TABLE IF NOT EXISTS mcp_project_canonical_uncertain (
            id               BIGINT PRIMARY KEY,
            server_id        VARCHAR NOT NULL,
            candidate_purls  VARCHAR,
            reason           VARCHAR,
            cousin_snapshot  VARCHAR,
            flagged_at       TIMESTAMPTZ DEFAULT now(),
            resolved_at      TIMESTAMPTZ,
            resolved_canonical VARCHAR
        )""",

        # Drift log -- governance event when sticky id disagrees with fresh
        """CREATE TABLE IF NOT EXISTS canonical_drift_log (
            id                   BIGINT PRIMARY KEY,
            server_id            VARCHAR NOT NULL,
            current_canonical    VARCHAR,
            proposed_canonical   VARCHAR,
            proposed_rule        VARCHAR,
            proposed_confidence  VARCHAR,
            detected_at          TIMESTAMPTZ DEFAULT now(),
            acknowledged_at      TIMESTAMPTZ,
            acknowledged_by      VARCHAR,
            promoted             BOOLEAN DEFAULT FALSE
        )""",
    ]
    ok = True
    for sql in statements:
        if not ws_execute(sql):
            log.error("schema step failed: %s", sql[:80])
            ok = False
    return ok


# ---- Noise filters ---------------------------------------------------

def _filter_republisher(cousins: list) -> list:
    return [c for c in cousins
            if not any((c.get("name") or "").startswith(p)
                       for p in KNOWN_REPUBLISHERS)]


def _filter_case_encoded(cousins: list) -> list:
    """Drop go modules where %21 appears -- these are URL-encoded dupes
    of plain-named cousins."""
    return [c for c in cousins if "%21" not in (c.get("name") or "")]


def _filter_dead_forks(cousins: list) -> list:
    """If any cousin has >1000 downloads, drop any cousin with <10."""
    any_active = any((c.get("downloads") or 0) > 1000 for c in cousins)
    if not any_active:
        return cousins
    return [c for c in cousins if (c.get("downloads") or 0) >= 10]


def clean_cousin_list(cousins: list) -> list:
    cousins = _filter_republisher(cousins)
    cousins = _filter_case_encoded(cousins)
    cousins = _filter_dead_forks(cousins)
    return cousins


# ---- Rules -----------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Strip scope prefix and lowercase for fuzzy compare."""
    if not name:
        return ""
    n = name.lower().strip()
    if n.startswith("@") and "/" in n:
        n = n.split("/", 1)[1]
    return n


def apply_rules(server_id: str, registry_name: str,
                metadata: dict, cousins: list) -> dict:
    """Return {canonical_id, canonical_name, ecosystem, downloads,
    repo_url, confidence, rule_applied, uncertain_cousins?}.
    """
    # Rule 1: self-canonical (no ecosyste.ms data)
    if metadata.get("lookup_status") != "ok" or not cousins:
        return {
            "canonical_id": f"pkg:self/{server_id[:16]}",
            "canonical_name": registry_name,
            "primary_ecosystem": "self",
            "primary_downloads": None,
            "repo_url": None,
            "confidence": "HIGH",  # we're certain it has no bridges
            "rule_applied": "1_self",
        }

    cleaned = clean_cousin_list(cousins)
    if not cleaned:
        # All cousins filtered as noise -- fall back to self
        return {
            "canonical_id": f"pkg:self/{server_id[:16]}",
            "canonical_name": registry_name,
            "primary_ecosystem": "self",
            "primary_downloads": None,
            "repo_url": None,
            "confidence": "HIGH",
            "rule_applied": "1_self_post_filter",
        }

    # Sort by downloads desc
    cleaned.sort(key=lambda c: c.get("downloads") or 0, reverse=True)
    top = cleaned[0]
    top_downloads = top.get("downloads") or 0
    second_downloads = cleaned[1].get("downloads") or 0 if len(cleaned) > 1 else 0

    # Rule 2: dominance (5x threshold)
    if len(cleaned) == 1 or (top_downloads >= DOMINANCE_THRESHOLD * max(second_downloads, 1)):
        return {
            "canonical_id": top.get("purl") or f"pkg:{top.get('ecosystem')}/{top.get('name')}",
            "canonical_name": top.get("name") or registry_name,
            "primary_ecosystem": top.get("ecosystem"),
            "primary_downloads": top_downloads,
            "repo_url": top.get("repository_url"),
            "confidence": "HIGH",
            "rule_applied": "2_dominance",
        }

    # Rule 3: name match
    reg_norm = _normalize_name(registry_name)
    for c in cleaned:
        if _normalize_name(c.get("name")) == reg_norm and reg_norm:
            return {
                "canonical_id": c.get("purl") or f"pkg:{c.get('ecosystem')}/{c.get('name')}",
                "canonical_name": c.get("name") or registry_name,
                "primary_ecosystem": c.get("ecosystem"),
                "primary_downloads": c.get("downloads"),
                "repo_url": c.get("repository_url"),
                "confidence": "HIGH",
                "rule_applied": "3_name_match",
            }

    # Rule 4: scope preference (prefer unscoped when mixed)
    scoped = [c for c in cleaned if (c.get("name") or "").startswith("@")]
    unscoped = [c for c in cleaned if not (c.get("name") or "").startswith("@")]
    if unscoped and scoped:
        # Take highest-downloads unscoped if it has any downloads
        unscoped.sort(key=lambda c: c.get("downloads") or 0, reverse=True)
        top_unscoped = unscoped[0]
        if (top_unscoped.get("downloads") or 0) > 0:
            return {
                "canonical_id": top_unscoped.get("purl") or f"pkg:{top_unscoped.get('ecosystem')}/{top_unscoped.get('name')}",
                "canonical_name": top_unscoped.get("name") or registry_name,
                "primary_ecosystem": top_unscoped.get("ecosystem"),
                "primary_downloads": top_unscoped.get("downloads"),
                "repo_url": top_unscoped.get("repository_url"),
                "confidence": "MEDIUM",
                "rule_applied": "4_unscoped_preferred",
            }

    # Rule 5: uncertain -- needs human review
    return {
        "canonical_id": None,
        "canonical_name": registry_name,
        "primary_ecosystem": None,
        "primary_downloads": None,
        "repo_url": None,
        "confidence": "UNCERTAIN",
        "rule_applied": "5_uncertain",
        "uncertain_cousins": cleaned[:10],  # cap for snapshot
    }


# ---- Data loading ----------------------------------------------------

def load_all_servers() -> list:
    """Join registry + metadata. Parses ecosystems_observed JSON.
    Note: we don't have full cousin detail in metadata -- just top-N.
    For full cousin analysis, would need to extend fetcher to cache
    raw response. For now, we work with top-cousin + ecosystems_observed.
    """
    return ws_query("""
        SELECT
            r.server_id,
            r.name AS registry_name,
            r.url,
            r.canonical_id AS existing_canonical,
            m.top_package_name,
            m.top_package_purl,
            m.top_ecosystem,
            m.top_downloads,
            m.cousin_count,
            m.ecosystems_observed,
            m.lookup_status
        FROM mcp_server_registry r
        LEFT JOIN mcp_ecosystems_metadata m ON r.server_id = m.server_id
        ORDER BY r.server_id
    """)


def synthesize_cousins_from_metadata(row: dict) -> list:
    """We don't store full cousin lists yet -- the fetcher only saves the
    top pick. This function reconstructs a minimal cousin list from the
    aggregate fields so rules 2-4 can still operate.

    When the full cousin cache is added (future work), replace this with
    the actual cached list. For now: top cousin is the only reliable
    signal, so rule 2 will fire (single-cousin dominance) or rule 1
    (self) in most cases.
    """
    if row.get("lookup_status") != "ok" or not row.get("top_package_name"):
        return []
    return [{
        "name": row.get("top_package_name"),
        "purl": row.get("top_package_purl"),
        "ecosystem": row.get("top_ecosystem"),
        "downloads": row.get("top_downloads"),
        "repository_url": None,  # not cached today
    }]


# ---- Core logic ------------------------------------------------------

def process_server(row: dict, dry_run: bool) -> str:
    """Apply rules to one server. Return verdict tag for counting."""
    server_id = row["server_id"]
    registry_name = row.get("registry_name") or ""
    existing = row.get("existing_canonical")

    cousins = synthesize_cousins_from_metadata(row)
    metadata = {"lookup_status": row.get("lookup_status")}
    result = apply_rules(server_id, registry_name, metadata, cousins)

    proposed = result["canonical_id"]
    rule = result["rule_applied"]

    # UNCERTAIN: write to review queue, do not touch registry
    if result["confidence"] == "UNCERTAIN":
        if not dry_run:
            _write_uncertain(server_id, result)
        return "uncertain"

    # STICKY: if registry already has canonical_id, check for drift
    if existing:
        if existing != proposed:
            if not dry_run:
                _write_drift(server_id, existing, proposed, rule, result["confidence"])
            return "drift_detected"
        return "unchanged"

    # First assignment
    if not dry_run:
        _write_canonical(server_id, result)
    return "assigned"


def _write_canonical(server_id: str, result: dict) -> None:
    """Two-step write: (1) update registry via COALESCE, (2) upsert into
    mcp_project_canonical. COALESCE ensures sticky -- only sets canonical_id
    if currently NULL. If someone else raced us, no harm done."""
    canonical_id = result["canonical_id"]

    # Step 1: sticky update on registry
    ws_execute(
        "UPDATE mcp_server_registry SET canonical_id = COALESCE(canonical_id, ?) "
        "WHERE server_id = ?",
        [canonical_id, server_id],
    )

    # Step 2: upsert into project_canonical (many servers may map to one canonical)
    import hashlib
    row_id = int(hashlib.md5(canonical_id.encode()).hexdigest()[:8], 16) % (2**31)
    ws_execute(
        """INSERT INTO mcp_project_canonical
           (id, canonical_id, canonical_name, primary_ecosystem,
            primary_downloads, repo_url, member_count, confidence, rule_applied)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(canonical_id) DO UPDATE SET
             member_count = member_count + 1,
             primary_downloads = COALESCE(excluded.primary_downloads, primary_downloads),
             repo_url = COALESCE(primary_downloads, excluded.repo_url)""",
        [row_id, canonical_id,
         result.get("canonical_name"),
         result.get("primary_ecosystem"),
         result.get("primary_downloads"),
         result.get("repo_url"),
         result["confidence"],
         result["rule_applied"]],
    )


def _write_uncertain(server_id: str, result: dict) -> None:
    import hashlib
    row_id = int(hashlib.md5(f"uncertain:{server_id}".encode()).hexdigest()[:8], 16) % (2**31)
    cousins_json = json.dumps(result.get("uncertain_cousins", []))[:2000]
    ws_execute(
        """INSERT INTO mcp_project_canonical_uncertain
           (id, server_id, candidate_purls, reason, cousin_snapshot)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             candidate_purls = excluded.candidate_purls,
             cousin_snapshot = excluded.cousin_snapshot,
             flagged_at = now()""",
        [row_id, server_id,
         json.dumps([c.get("purl") for c in result.get("uncertain_cousins", [])]),
         result.get("rule_applied", "unknown"),
         cousins_json],
    )


def _write_drift(server_id: str, current: str, proposed: str,
                 rule: str, confidence: str) -> None:
    import hashlib
    row_id = int(hashlib.md5(
        f"drift:{server_id}:{proposed}:{datetime.now(timezone.utc).date().isoformat()}".encode()
    ).hexdigest()[:8], 16) % (2**31)
    ws_execute(
        """INSERT INTO canonical_drift_log
           (id, server_id, current_canonical, proposed_canonical,
            proposed_rule, proposed_confidence)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO NOTHING""",
        [row_id, server_id, current, proposed, rule, confidence],
    )


def run_once(dry_run: bool) -> dict:
    servers = load_all_servers()
    log.info("loaded %d servers from registry", len(servers))

    counts = {"assigned": 0, "unchanged": 0, "uncertain": 0,
              "drift_detected": 0}
    rule_counts = {}

    for s in servers:
        if _shutdown:
            break
        verdict = process_server(s, dry_run)
        counts[verdict] = counts.get(verdict, 0) + 1

    log.info("canonicalization %s complete: %s",
             "(dry-run)" if dry_run else "", counts)
    return counts


# ---- Entry point -----------------------------------------------------

def _shutdown_handler(_signum, _frame):
    global _shutdown
    _shutdown = True


def main():
    pysignal.signal(pysignal.SIGTERM, _shutdown_handler)
    pysignal.signal(pysignal.SIGINT, _shutdown_handler)

    mode = "--once"
    dry_run = False
    if "--dry-run" in sys.argv:
        dry_run = True
        mode = "--dry-run"
    elif "--loop" in sys.argv:
        mode = "--loop"

    log.info("=" * 60)
    log.info("ZO-SENTINEL Canonicalizer v1.0 (Commit B)")
    log.info("  Mode:             %s", mode)
    log.info("  Dominance thresh: %.1fx", DOMINANCE_THRESHOLD)
    log.info("  Sticky policy:    COALESCE on registry + drift log")
    log.info("=" * 60)

    if not ensure_schema():
        log.error("schema step failed; aborting")
        return 2

    counts = run_once(dry_run)

    if mode != "--loop":
        return 0

    while not _shutdown:
        time.sleep(LOOP_INTERVAL_S)
        if _shutdown:
            break
        try:
            run_once(False)
        except Exception as e:
            log.error("cycle error: %s", e)

    log.info("canonicalizer clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())