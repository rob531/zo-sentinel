#!/usr/bin/env python3
"""
axis_score_delta_scoring_consumer.py -- Daemon that detects axis-score state changes
between consecutive scoring runs and emits delta signals.

Reads McpLlmAxisScore rows for each server across two time-adjacent runs,
computes per-axis score deltas, and emits a delta blob to mcp_signal_enrichments.
"""

# deps: requests

import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("axis_score_delta_consumer")

WRITE_SERVICE = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL_SECONDS = 60
POLL_INTERVAL_SECONDS = 60
HTTP_TIMEOUT = 10
WRITE_TIMEOUT = 30

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

# Tier ordering for tier_changed detection
_TIER_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]


def _tier_index(label: Optional[str]) -> int:
    lbl = (label or "UNKNOWN").upper()
    try:
        return _TIER_ORDER.index(lbl)
    except ValueError:
        return 4  # UNKNOWN


def compute_delta(
    prev_scores: List[dict], curr_scores: List[dict]
) -> dict:
    """
    Compute per-axis score deltas between two consecutive scoring runs.

    prev_scores / curr_scores: list of {axis_name, p_top, p_critical, p_danger, label_index} rows.

    Returns {
        server_id, prev_run_id, curr_run_id,
        axes: {axis_name: {delta_p_top, delta_p_critical, delta_p_danger, tier_changed}},
        overall_delta, changed_axes_count, emitted_at
    }
    """
    prev_map = {r["axis_name"]: r for r in prev_scores}
    curr_map = {r["axis_name"]: r for r in curr_scores}

    axes_out: Dict[str, Dict[str, Any]] = {}
    total_delta = 0.0
    changed_count = 0

    for ax in AXES:
        p = prev_map.get(ax, {})
        c = curr_map.get(ax, {})

        delta_p_top = (c.get("p_top") or 0.0) - (p.get("p_top") or 0.0)
        delta_p_critical = (c.get("p_critical") or 0.0) - (p.get("p_critical") or 0.0)
        delta_p_danger = (c.get("p_danger") or 0.0) - (p.get("p_danger") or 0.0)

        prev_label = p.get("label")
        curr_label = c.get("label")
        tier_changed = prev_label != curr_label

        axes_out[ax] = {
            "delta_p_top": round(delta_p_top, 6),
            "delta_p_critical": round(delta_p_critical, 6),
            "delta_p_danger": round(delta_p_danger, 6),
            "tier_changed": tier_changed,
        }

        total_delta += abs(delta_p_top)
        if tier_changed:
            changed_count += 1

    prev_ids = [r.get("id") for r in prev_scores if r.get("id")]
    curr_ids = [r.get("id") for r in curr_scores if r.get("id")]

    return {
        "axes": axes_out,
        "overall_delta": round(total_delta, 6),
        "changed_axes_count": changed_count,
        "prev_run_id": min(prev_ids) if prev_ids else None,
        "curr_run_id": min(curr_ids) if curr_ids else None,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }


def _ws_query(sql: str, params: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT via write_service /query."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    backoff = 1
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{WRITE_SERVICE}/query",
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            if 500 <= resp.status_code < 600:
                raise requests.HTTPError(f"{resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json().get("rows", [])
        except requests.RequestException as e:
            if attempt < 2:
                log.warning("Query failed (attempt %d/3), retrying in %ds: %s", attempt + 1, backoff, e)
                time.sleep(backoff)
                backoff *= 2
            else:
                raise
    return []


def _ws_write(rows: List[dict]) -> None:
    """Upsert rows to mcp_signal_enrichments via write_service /write."""
    payload = {"table": "mcp_signal_enrichments", "rows": rows, "wait": True}
    backoff = 1
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{WRITE_SERVICE}/write",
                json=payload,
                timeout=WRITE_TIMEOUT,
            )
            if 500 <= resp.status_code < 600:
                raise requests.HTTPError(f"{resp.status_code}", response=resp)
            resp.raise_for_status()
            return
        except requests.RequestException as e:
            if attempt < 2:
                log.warning("Write failed (attempt %d/3), retrying in %ds: %s", attempt + 1, backoff, e)
                time.sleep(backoff)
                backoff *= 2
            else:
                raise


def _send_heartbeat() -> None:
    """Fire-and-forget heartbeat to service_health."""
    try:
        requests.post(
            f"{WRITE_SERVICE}/health",
            json={"service": "axis_score_delta_scoring_consumer", "status": "running"},
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        pass


def _get_servers_to_scan() -> List[str]:
    """Return server_ids that have at least 2 distinct scoring runs."""
    sql = """
        SELECT DISTINCT server_id
        FROM McpLlmAxisScore
        WHERE axis_name = 'overall_risk'
          AND server_id IN (
              SELECT server_id FROM McpLlmAxisScore
              GROUP BY server_id
              HAVING COUNT(DISTINCT model_version) >= 1
          )
        LIMIT 500
    """
    return [r["server_id"] for r in _ws_query(sql)]


def _get_last_emitted_at(server_id: str) -> Optional[datetime]:
    """Return the most recent scored_at already emitted for this server."""
    sql = """
        SELECT evidence
        FROM mcp_signal_enrichments
        WHERE mcp_server_id = :sid
          AND enrichment_type = 'axis_score_delta'
        ORDER BY computed_at DESC
        LIMIT 1
    """
    rows = _ws_query(sql, {"sid": server_id})
    if not rows:
        return None
    blob = rows[0].get("evidence", {})
    emitted_str = blob.get("emitted_at")
    if emitted_str:
        try:
            return datetime.fromisoformat(emitted_str.replace("Z", "+00:00"))
        except Exception:
            pass
    return None


def _get_two_runs(server_id: str) -> tuple:
    """
    Return (prev_scores, curr_scores) for the two most-recent scoring runs.

    Each "run" = set of 7 axis rows sharing the same model_version (and scored_at).
    """
    sql = """
        SELECT id, server_id, axis_name, label, label_index,
               p_top, p_critical, p_danger, scored_at, model_version
        FROM McpLlmAxisScore
        WHERE server_id = :sid
        ORDER BY scored_at DESC, model_version DESC
    """
    rows = _ws_query(sql, {"sid": server_id})
    if not rows:
        return ([], [])

    # Group by model_version
    by_version: Dict[str, List[dict]] = {}
    for r in rows:
        mv = r.get("model_version") or "?"
        by_version.setdefault(mv, []).append(r)

    # Sort by latest scored_at per version
    sorted_versions = sorted(
        by_version.keys(),
        key=lambda mv: max(
            (r.get("scored_at") or "") for r in by_version[mv]
        ),
        reverse=True,
    )

    if len(sorted_versions) < 2:
        # Only one run — emit delta against empty prev
        curr = rows
        return ([], curr)

    prev_version = sorted_versions[1]
    curr_version = sorted_versions[0]
    prev = [r for r in rows if r.get("model_version") == prev_version]
    curr = [r for r in rows if r.get("model_version") == curr_version]
    return (prev, curr)


def run() -> None:
    """Main daemon loop."""
    log.info("Starting axis_score_delta_scoring_consumer")
    running = True

    def shutdown(signum, _frame):
        nonlocal running
        log.info("Received signal %d, shutting down", signum)
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    last_heartbeat = time.time()

    while running:
        cycle_start = time.time()
        processed = 0

        try:
            servers = _get_servers_to_scan()

            for sid in servers:
                if not running:
                    break

                try:
                    last_emitted = _get_last_emitted_at(sid)

                    prev_scores, curr_scores = _get_two_runs(sid)

                    if not curr_scores:
                        continue

                    # Skip if no new runs since last emission
                    curr_scored = max(
                        (r.get("scored_at") or "") for r in curr_scores
                    )
                    if last_emitted and curr_scored:
                        try:
                            curr_ts = datetime.fromisoformat(
                                curr_scored.replace("Z", "+00:00")
                            )
                            if curr_ts <= last_emitted:
                                continue
                        except Exception:
                            pass

                    delta = compute_delta(prev_scores, curr_scores)
                    delta["server_id"] = sid

                    row = {
                        "mcp_server_id": sid,
                        "enrichment_type": "axis_score_delta",
                        "evidence": delta,
                        "computed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _ws_write([row])
                    processed += 1
                    log.debug("Emitted delta for server_id=%s (changed_axes=%d)",
                              sid, delta["changed_axes_count"])

                except Exception as e:
                    log.warning("Error processing server %s: %s", sid, e)

            log.info("Cycle complete: %d delta rows emitted", processed)

        except Exception as e:
            log.error("Cycle error: %s", e)

        # Heartbeat
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            _send_heartbeat()
            last_heartbeat = now

        elapsed = time.time() - cycle_start
        sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    log.info("Daemon stopped")


if __name__ == "__main__":
    # Self-test: compute_delta with two synthetic runs
    prev_scores = [
        {"axis_name": "overall_risk",        "label": "MEDIUM",  "p_top": 0.3,  "p_critical": 0.1, "p_danger": 0.2, "label_index": 1, "id": 100},
        {"axis_name": "auth_strength",        "label": "STRONG",  "p_top": 0.8,  "p_critical": 0.0, "p_danger": 0.05, "label_index": 3, "id": 101},
        {"axis_name": "capability_breadth",   "label": "BROAD",   "p_top": 0.6,  "p_critical": 0.1, "p_danger": 0.2, "label_index": 2, "id": 102},
        {"axis_name": "data_sensitivity",     "label": "LOW",     "p_top": 0.1,  "p_critical": 0.0, "p_danger": 0.1, "label_index": 0, "id": 103},
        {"axis_name": "network_egress",       "label": "INTERNAL","p_top": 0.9,  "p_critical": 0.0, "p_danger": 0.01, "label_index": 4, "id": 104},
        {"axis_name": "maintainer_trust",    "label": "ESTABLISHED","p_top":0.7, "p_critical":0.05,"p_danger":0.1, "label_index":3, "id":105},
        {"axis_name": "exploit_surface",      "label": "LOW",     "p_top": 0.2,  "p_critical": 0.05,"p_danger": 0.15, "label_index":0, "id":106},
    ]
    # curr: overall_risk escalated MEDIUM->HIGH (p_top 0.3->0.7), auth_strength unchanged, others unchanged
    curr_scores = [
        {"axis_name": "overall_risk",        "label": "HIGH",    "p_top": 0.7,  "p_critical": 0.3, "p_danger": 0.5, "label_index": 2, "id": 200},
        {"axis_name": "auth_strength",        "label": "STRONG",  "p_top": 0.8,  "p_critical": 0.0, "p_danger": 0.05, "label_index": 3, "id": 201},
        {"axis_name": "capability_breadth",   "label": "BROAD",   "p_top": 0.6,  "p_critical": 0.1, "p_danger": 0.2, "label_index": 2, "id": 202},
        {"axis_name": "data_sensitivity",     "label": "LOW",     "p_top": 0.1,  "p_critical": 0.0, "p_danger": 0.1, "label_index": 0, "id": 203},
        {"axis_name": "network_egress",       "label": "INTERNAL","p_top": 0.9,  "p_critical": 0.0, "p_danger": 0.01, "label_index": 4, "id": 204},
        {"axis_name": "maintainer_trust",    "label": "ESTABLISHED","p_top":0.7, "p_critical":0.05,"p_danger":0.1, "label_index":3, "id":205},
        {"axis_name": "exploit_surface",      "label": "LOW",     "p_top": 0.2,  "p_critical": 0.05,"p_danger": 0.15, "label_index":0, "id":206},
    ]

    delta = compute_delta(prev_scores, curr_scores)

    # Assertions
    assert "axes" in delta, "delta must have 'axes'"
    assert "overall_delta" in delta, "delta must have 'overall_delta'"
    assert "changed_axes_count" in delta, "delta must have 'changed_axes_count'"

    # overall_risk changed MEDIUM->HIGH
    overall_ax = delta["axes"]["overall_risk"]
    assert overall_ax["tier_changed"] is True, f"overall_risk tier_changed should be True, got {overall_ax}"
    assert abs(overall_ax["delta_p_top"] - 0.4) < 1e-5, f"delta_p_top should be 0.4, got {overall_ax['delta_p_top']}"
    assert abs(overall_ax["delta_p_critical"] - 0.2) < 1e-5, f"delta_p_critical should be 0.2, got {overall_ax['delta_p_critical']}"

    # auth_strength unchanged
    auth_ax = delta["axes"]["auth_strength"]
    assert auth_ax["tier_changed"] is False, f"auth_strength tier_changed should be False, got {auth_ax}"
    assert abs(auth_ax["delta_p_top"]) < 1e-5, f"auth_strength delta_p_top should be 0, got {auth_ax['delta_p_top']}"

    assert delta["changed_axes_count"] == 1, f"changed_axes_count should be 1, got {delta['changed_axes_count']}"
    assert delta["overall_delta"] > 0, f"overall_delta should be positive, got {delta['overall_delta']}"

    # Test empty prev (first run vs no history)
    delta_empty = compute_delta([], curr_scores)
    assert delta_empty["prev_run_id"] is None, "prev_run_id should be None for empty prev"
    assert delta_empty["curr_run_id"] == 200, f"curr_run_id should be 200, got {delta_empty['curr_run_id']}"

    print("PASS axis_score_delta_scoring_consumer")
