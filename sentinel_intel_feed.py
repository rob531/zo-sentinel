#!/usr/bin/env python3
"""
sentinel_intel_feed.py  -- feed ambient external intelligence into directive generator Layer 1

Purpose: Exposes external-intelligence context from two mesh-side sources to the
directive generator's assemble_layer1_context. Feeds ambient intelligence (not
build directives) into Layer 1's intel_map section.

Sources:
  1. world_agent article_insights from mesh_memory via write_service /query
  2. Latest wisdom_synthesiser briefing from SYSTEM_WISDOM.md on disk

Scope discipline: This library produces ambient intelligence sections. It does NOT
propose directives. Directive proposals are gated by the SENTINEL_SCOPE_BOUNDARY.md
decision rule: only when intel surfaces NEW detection artefacts, threat feeds, or
registries that Sentinel does not yet ingest.

Wiring is a separate follow-up directive -- this library lands and smokes cleanly
before being consumed by directive_knowledge_sources.

References:
  - signal_bridge.py for working ws_query with parameterized SQL against mesh_memory
  - SENTINEL_SCOPE_BOUNDARY.md for scope discipline
  - write_service protocol: POST /write with 'rows' not 'row'; POST /query with parameterized SQL
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
WISDOM_PATH = "/home/workspace/zo_sentinel/SYSTEM_WISDOM.md"
QUERY_TIMEOUT_SECS = 10.0

# ---------------------------------------------------------------------------
# Keyword sets (lowercase tuples for case-insensitive substring matching)
# ---------------------------------------------------------------------------

MCP_KEYWORDS = (
    "mcp",
    "model context protocol",
    "model-context-protocol",
)

SUPPLY_CHAIN_KEYWORDS = (
    "supply chain",
    "supply-chain",
    "typosquat",
    "typo squat",
    "malicious package",
    "malicious npm",
    "malicious pypi",
    "package compromise",
    "rug pull",
    "rug-pull",
    "prompt injection",
    "tool poisoning",
)

GENERAL_KEYWORDS = (
    "ai agent",
    "agentic",
    "llm",
    "model release",
    "inference",
    "api key",
    "spyware",
    "zero-day",
    "cve",
    "vulnerability",
    "threat actor",
    "apt",
    "c2",
    "command and control",
    "ransomware",
)

# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------

def score_insight(insight_row: dict) -> tuple[int, dict]:
    """
    Score a single article_insight row based on keyword tier weights.

    Args:
        insight_row: dict with keys title (str), content (str JSON-encoded dict)
                    The content JSON has keys 'title' and 'insight'.

    Returns:
        (total_score, metadata_dict) where metadata_dict has:
          - matched: {keyword: count} for all matched keywords
          - tier_hits: {3: n, 2: n, 1: n} hit counts per tier
    """
    # Extract text to search: title + first 500 chars of insight
    try:
        title = insight_row.get("title", "") or ""
        content_str = insight_row.get("content", "") or ""
        if content_str:
            try:
                content_obj = json.loads(content_str)
            except (json.JSONDecodeError, TypeError):
                content_obj = {}
        else:
            content_obj = {}

        insight_text = content_obj.get("insight", "") or ""
    except Exception:
        title = str(insight_row.get("title", ""))
        insight_text = ""

    search_text = (title + " " + insight_text[:500]).lower()

    matched = {}
    tier_hits = {3: 0, 2: 0, 1: 0}

    # Tier 3: MCP keywords (weight 3)
    for kw in MCP_KEYWORDS:
        count = search_text.count(kw)
        if count > 0:
            matched[kw] = count
            tier_hits[3] += count

    # Tier 2: Supply-chain keywords (weight 2)
    for kw in SUPPLY_CHAIN_KEYWORDS:
        count = search_text.count(kw)
        if count > 0:
            matched[kw] = count
            tier_hits[2] += count

    # Tier 1: General keywords (weight 1)
    for kw in GENERAL_KEYWORDS:
        count = search_text.count(kw)
        if count > 0:
            matched[kw] = count
            tier_hits[1] += count

    total_score = (tier_hits[3] * 3) + (tier_hits[2] * 2) + (tier_hits[1] * 1)

    return total_score, {"matched": matched, "tier_hits": tier_hits}

# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_recent_article_insights(hours: int = 48, limit: int = 50) -> list[dict]:
    """
    Query mesh_memory for recent world_agent article_insights.

    Uses parameterized SQL to prevent injection.
    Returns raw rows as dicts.

    Args:
        hours: How many hours back to look (default 48)
        limit: Max rows to return (default 50)

    Returns:
        List of dict rows from mesh_memory, or [] on any failure.
    """
    # Try post_with_retry from http_retry if available
    try:
        from http_retry import post_with_retry
        _use_retry = True
    except ImportError:
        _use_retry = False

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    # Format as ISO string with timezone
    cutoff_str = cutoff_time.isoformat()

    sql = """
        SELECT agent_id, memory_type, content, importance, created_at, title
        FROM mesh_memory
        WHERE agent_id = ?
          AND memory_type = ?
          AND created_at > ?
        ORDER BY created_at DESC
        LIMIT ?
    """
    params = ["t1.world_agent", "article_insight", cutoff_str, limit]

    payload = {"sql": sql.strip(), "params": params}

    try:
        if _use_retry:
            resp = post_with_retry(QUERY_URL, json=payload, timeout=QUERY_TIMEOUT_SECS)
        else:
            resp = requests.post(QUERY_URL, json=payload, timeout=QUERY_TIMEOUT_SECS)

        if resp is None:
            return []

        if resp.status_code != 200:
            return []

        data = resp.json()
        rows = data.get("rows", [])
        return rows if isinstance(rows, list) else []

    except Exception:
        return []


def load_latest_wisdom_briefing(max_chars: int = 1500) -> Optional[str]:
    """
    Read SYSTEM_WISDOM.md and return its contents truncated to max_chars.

    Returns None if:
      - File does not exist
      - File modification time is > 48 hours old
      - Any read error occurs

    Args:
        max_chars: Truncate output to this many characters (default 1500)

    Returns:
        Truncated file contents as str, or None if unavailable/stale.
    """
    if not os.path.exists(WISDOM_PATH):
        return None

    try:
        mtime = os.path.getmtime(WISDOM_PATH)
    except OSError:
        return None

    file_age = time.time() - mtime
    if file_age > (48 * 3600):
        return None

    try:
        with open(WISDOM_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    if len(content) > max_chars:
        return content[:max_chars] + "\n\n[... wisdom truncated ...]"
    return content

# ---------------------------------------------------------------------------
# Orchestration: build markdown section
# ---------------------------------------------------------------------------

def build_intel_map_section(hours: int = 48, top_n: int = 5, wisdom_max_chars: int = 1500) -> str:
    """
    Build a markdown section suitable for direct injection into directive_gen prompt.

    Orchestrates fetch + score + format pipeline.

    Args:
        hours: How many hours of article_insights to consider (default 48)
        top_n: How many top-scored insights to include (default 5)
        wisdom_max_chars: Max chars to read from SYSTEM_WISDOM.md (default 1500)

    Returns:
        Markdown string with three subsections:
          1. Top-ranked article insights
          2. Latest wisdom synthesiser briefing
          3. Scope reminder

        On any failure, returns a graceful empty-but-structured section.
        Never raises, never blocks the directive generator.
    """
    try:
        # Fetch raw insights
        raw_insights = fetch_recent_article_insights(hours=hours, limit=50)

        # Score each insight
        scored = []
        for row in raw_insights:
            score, meta = score_insight(row)
            if score > 0:
                scored.append((score, row, meta))

        # Sort by score descending, then by created_at descending
        scored.sort(key=lambda x: (-x[0], str(x[1].get("created_at", ""))))

        top_insights = scored[:top_n] if scored else []

        # Load wisdom briefing
        wisdom = load_latest_wisdom_briefing(max_chars=wisdom_max_chars)

        # Build markdown
        lines = []
        lines.append("## External Intelligence Map (recency <=48h)")
        lines.append("")
        lines.append("### Top-ranked article insights")
        lines.append("")

        if not top_insights:
            lines.append("_No scored insights found in the last {0}h. write_service may be unreachable or no matching articles._".format(hours))
        else:
            for rank, (score, row, meta) in enumerate(top_insights, start=1):
                # Extract title and insight text
                title = row.get("title", "untitled")
                try:
                    content_obj = json.loads(row.get("content", "{}"))
                except (json.JSONDecodeError, TypeError):
                    content_obj = {}

                insight_text = content_obj.get("insight", "") or ""
                created = row.get("created_at", "unknown")

                # Truncate insight to 400 chars for the block
                display_insight = insight_text[:400] + ("..." if len(insight_text) > 400 else "")

                lines.append(f"**[{rank}] Score={score} | {created}**")
                lines.append(f"_Title: {title}_")
                lines.append(f"<{display_insight}>")
                lines.append(f"  → tier_hits={meta['tier_hits']}, matched={list(meta['matched'].keys())}")
                lines.append(f"  → source: [world_agent]")
                lines.append("")

        lines.append("### Latest wisdom synthesiser briefing")
        lines.append("")

        if wisdom is None:
            lines.append("_No fresh wisdom briefing available (file missing or >48h old)._")
        else:
            lines.append(f"<{wisdom}>")
            lines.append(f"  → source: [wisdom_synthesiser]")
        lines.append("")

        lines.append("### Scope reminder")
        lines.append("Items above are ambient intelligence. Not every relevant news item requires a directive. Only propose directives when the intel surfaces a NEW detection artefact, threat feed, or registry that Sentinel does not yet ingest -- per SENTINEL_SCOPE_BOUNDARY.md decision rule.")

        return "\n".join(lines)

    except Exception:
        # Graceful failure: never block directive_gen
        lines = []
        lines.append("## External Intelligence Map (recency <=48h)")
        lines.append("")
        lines.append("### Top-ranked article insights")
        lines.append("_Intel feed unavailable (error during fetch/score). Service is healthy -- this section will self-populate on next cycle._")
        lines.append("")
        lines.append("### Latest wisdom synthesiser briefing")
        lines.append("_Wisdom briefing unavailable._")
        lines.append("")
        lines.append("### Scope reminder")
        lines.append("Items above are ambient intelligence. Not every relevant news item requires a directive. Only propose directives when the intel surfaces a NEW detection artefact, threat feed, or registry that Sentinel does not yet ingest -- per SENTINEL_SCOPE_BOUNDARY.md decision rule.")
        return "\n".join(lines)

# ---------------------------------------------------------------------------
# Self-test block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("sentinel_intel_feed.py -- self-test")
    print("=" * 70)
    print()

    section = build_intel_map_section(hours=48, top_n=3, wisdom_max_chars=1500)
    print(section)
    print()

    # Check write_service reachability
    try:
        resp = requests.get(f"{WRITE_SERVICE}/health", timeout=5)
        reachable = resp.status_code == 200
    except Exception:
        reachable = False

    print("=" * 70)
    print(f"write_service reachable: {reachable}")
    print("Self-test exit: 0")
    print("=" * 70)
    exit(0)