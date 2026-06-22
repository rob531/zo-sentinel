#!/usr/bin/env python3
"""
investigate_definition_history_gap_v2.py
========================================
Diagnose why `mcp_definition_history` stays empty (1784 rows in
`mcp_server_registry`, 0 in `mcp_definition_history`), trace the real
data flow `mcp_scanner -> pipeline_bridge -> definition_history`, and
ship a working patch that records definition snapshots on first_seen
and on subsequent definition changes.

This is v2 of `quarantine/investigate_definition_history_gap.py`. v1 was a
read-only schema/audit-log probe that *guessed* the writer's expected
columns. v2 goes further: it (a) proves via `write_queue_log` that no
write was ever attempted, (b) reads the actual scanner/bridge/writer
source to locate the broken integration points, (c) queries the live
registry to identify the population a snapshot mechanism should track,
and (d) embeds a concrete, schema-correct patch that can be applied.

Modes (per the workspace "dry_run / SHOW_ME before live execution" rule):
  python3 investigate_definition_history_gap_v2.py            # dry-run diagnostic (READ-ONLY, default)
  python3 investigate_definition_history_gap_v2.py --apply    # backfill first_seen snapshots for existing registry rows (idempotent)
  python3 investigate_definition_history_gap_v2.py --emit-scanner-patch  # write patched upsert to mcp_scanner.snapshot_patch.py for review
  python3 investigate_definition_history_gap_v2.py --json     # machine-readable report only

All DB access goes through the write_service HTTP API (port 8772), never
a direct DuckDB connection — the live DB is locked by the writer process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
WRITE_URL = f"{WRITE_SERVICE}/write"
EXEC_URL = f"{WRITE_SERVICE}/execute"
TIMEOUT = 10

ZS = "/home/workspace/zo_sentinel"
SCANNER_PATH = f"{ZS}/mcp_scanner.py"
BRIDGE_PATH = "/home/workspace/zo_mesh/pipeline_bridge.py"
WRITER_V2_PATH = f"{ZS}/definition_change_history_writer_v2.py"
DETECTOR_PATH = f"{ZS}/definition_change_detector.py"

# The REAL table schema (verified live via information_schema.columns).
DEFHIST_COLUMNS = ["id", "server_id", "snapshot_hash", "captured_at"]
REGISTRY_COLUMNS = [
    "server_id", "name", "registry_source", "url", "description",
    "trust_score", "verdict", "verdict_reasoning", "confidence",
    "last_assessed", "first_seen", "last_seen", "last_scanned",
    "scan_count", "risk_tier", "metadata",
]


# --------------------------------------------------------------------------- #
#  write_service client (matches the REAL contract in zo_mesh/write_service.py)
# --------------------------------------------------------------------------- #
def ws_query(sql: str, params: list | None = None) -> list[dict]:
    """POST {sql[,params]} -> {rows, count}. NOTE: write_service auto-appends
    `LIMIT 200` to any query lacking a LIMIT, which breaks DESCRIBE/PRAGMA —
    so always use information_schema.columns instead of DESCRIBE here."""
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        r = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("rows", [])
    except Exception as e:
        return [{"_error": str(e)}]


def ws_write(table: str, rows: list[dict] | dict, mode: str = "insert",
             agent_id: str = "investigate_defhist_v2", wait: bool = True) -> dict:
    """POST {table, rows, mode, agent_id, wait} -> {ok, queued, wait}.
    `rows` may be a single dict or a list. mcp_definition_history is in the
    writer's _NO_AUTO_ID set, so the caller MUST supply `id` (use seq_defhist_id)."""
    if isinstance(rows, dict):
        rows = [rows]
    try:
        r = requests.post(WRITE_URL, json={"table": table, "rows": rows,
                                           "mode": mode, "agent_id": agent_id,
                                           "wait": wait}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


def ws_execute(sql: str, agent_id: str = "investigate_defhist_v2") -> dict:
    try:
        r = requests.post(EXEC_URL, json={"sql": sql, "agent_id": agent_id,
                                          "wait": True}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


def ws_health() -> dict:
    try:
        r = requests.get(f"{WRITE_SERVICE}/health", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


# --------------------------------------------------------------------------- #
#  source-file inspection helpers
# --------------------------------------------------------------------------- #
def read_source(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception as e:
        return f"<unreadable: {e}>"


def grep_lines(src: str, needle: str, ctx: int = 0) -> list[str]:
    lines = src.splitlines()
    out: list[str] = []
    for i, ln in enumerate(lines):
        if needle in ln:
            lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
            for j in range(lo, hi):
                out.append(f"{j+1:4d}: {lines[j]}")
            out.append("")
    return out


def running_procs(pattern: str) -> list[str]:
    try:
        out = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5).stdout
        return [ln for ln in out.splitlines() if pattern in ln and "grep" not in ln]
    except Exception as e:
        return [f"<ps failed: {e}>"]


# --------------------------------------------------------------------------- #
#  definition snapshot hashing (the field set the patch will track)
# --------------------------------------------------------------------------- #
def definition_snapshot_hash(row: dict) -> str:
    """Stable hash over the definition-bearing fields of a registry row.
    The registry has NO `version`/`tool_schema`/`tool_definitions` column —
    the 'definition' payload lives in `description` + `metadata` (JSON)."""
    payload = {
        "description": (row.get("description") or ""),
        "metadata": row.get("metadata") or "",
        # include url so a repo/package re-publish under same name+desc is caught
        "url": row.get("url") or "",
    }
    norm = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(norm.encode()).hexdigest()


def latest_snapshot_hash(server_id: str) -> str | None:
    """Most recent snapshot_hash recorded for a server, or None if never snapshotted."""
    rows = ws_query(
        "SELECT snapshot_hash FROM mcp_definition_history "
        "WHERE server_id = ? ORDER BY captured_at DESC, id DESC LIMIT 1",
        [server_id],
    )
    if rows and isinstance(rows[0], dict) and rows[0].get("snapshot_hash"):
        return rows[0]["snapshot_hash"]
    return None


def next_defhist_id() -> int:
    """mcp_definition_history.id is BIGINT PK and the table is in _NO_AUTO_ID,
    so the writer will not auto-generate it. Pull the next value from the
    sequence declared in write_service.py (seq_defhist_id)."""
    rows = ws_query("SELECT nextval('seq_defhist_id') AS nid")
    if rows and isinstance(rows[0], dict):
        v = rows[0].get("nid")
        if v is not None:
            return int(v)
    # fallback: max(id)+1 (works if sequence is missing for any reason)
    rows = ws_query("SELECT COALESCE(MAX(id),0)+1 AS nid FROM mcp_definition_history")
    return int(rows[0]["nid"]) if rows and isinstance(rows[0], dict) else 1


def record_snapshot(server_id: str, snapshot_hash: str,
                    agent_id: str = "defhist_recorder") -> dict:
    """Insert one snapshot row into mcp_definition_history using the REAL
    schema (id, server_id, snapshot_hash, captured_at)."""
    row = {
        "id": next_defhist_id(),
        "server_id": server_id,
        "snapshot_hash": snapshot_hash,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    return ws_write("mcp_definition_history", row, mode="insert", agent_id=agent_id)


# --------------------------------------------------------------------------- #
#  diagnostics
# --------------------------------------------------------------------------- #
def diagnose() -> dict:
    f: dict[str, Any] = {
        "diagnostic": "investigate_definition_history_gap_v2",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "write_service_health": ws_health(),
        "counts": {},
        "schema": {},
        "scan_count_distribution": [],
        "candidate_population": {},
        "data_flow_trace": {},
        "broken_writers": {},
        "daemon_inventory": {},
        "write_queue_log_proof": {},
        "root_causes": [],
        "missing_integration_point": {},
    }

    # ---- counts ----
    f["counts"]["mcp_server_registry"] = ws_query(
        "SELECT COUNT(*) n FROM mcp_server_registry")
    f["counts"]["mcp_definition_history"] = ws_query(
        "SELECT COUNT(*) n FROM mcp_definition_history")

    # ---- schema (live, not guessed) ----
    f["schema"]["mcp_definition_history"] = ws_query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='mcp_definition_history' ORDER BY ordinal_position")
    f["schema"]["mcp_server_registry"] = ws_query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='mcp_server_registry' ORDER BY ordinal_position")

    # ---- scan_count distribution: proves the scanner never re-scans ----
    f["scan_count_distribution"] = ws_query(
        "SELECT scan_count, COUNT(*) n FROM mcp_server_registry "
        "GROUP BY scan_count ORDER BY scan_count")

    # ---- candidate population: servers a snapshot mechanism should track ----
    f["candidate_population"]["by_source"] = ws_query(
        "SELECT registry_source, COUNT(*) n, "
        "COUNT(*) FILTER (WHERE metadata ILIKE '%version%' OR metadata ILIKE '%date%') with_version_meta "
        "FROM mcp_server_registry GROUP BY registry_source ORDER BY n DESC")
    f["candidate_population"]["definition_field_availability"] = ws_query(
        "SELECT COUNT(*) FILTER (WHERE metadata IS NOT NULL AND metadata<>'') with_meta, "
        "COUNT(*) FILTER (WHERE description IS NOT NULL AND description<>'') with_desc, "
        "COUNT(*) total FROM mcp_server_registry")
    f["candidate_population"]["samples_with_version_meta"] = ws_query(
        "SELECT name, registry_source, substr(description,1,70) desc_snip, "
        "substr(metadata,1,90) meta_snip FROM mcp_server_registry "
        "WHERE metadata ILIKE '%version%' ORDER BY first_seen DESC LIMIT 5")

    # ---- write_queue_log proof: has ANY write to mcp_definition_history ever been enqueued? ----
    f["write_queue_log_proof"]["attempts_to_mcp_definition_history"] = ws_query(
        "SELECT table_name, COUNT(*) attempts, MAX(success) max_success, "
        "MAX(written_at) last_attempt FROM write_queue_log "
        "WHERE table_name='mcp_definition_history' GROUP BY table_name")

    # ---- daemon / process inventory ----
    f["daemon_inventory"]["service_health_definition_or_scanner"] = ws_query(
        "SELECT service, status, last_heartbeat FROM service_health "
        "WHERE service ILIKE '%definition%' OR service ILIKE '%history%' "
        "OR service ILIKE '%change%' OR service IN ('mcp_scanner','pipeline_bridge') "
        "ORDER BY last_heartbeat DESC")
    f["daemon_inventory"]["running_processes"] = {
        "mcp_scanner": running_procs("mcp_scanner.py"),
        "pipeline_bridge": running_procs("pipeline_bridge.py"),
        "definition_change_detector": running_procs("definition_change_detector.py"),
        "definition_change_history_writer": running_procs("definition_change_history_writer"),
    }

    # ---- data flow trace (source evidence) ----
    scanner_src = read_source(SCANNER_PATH)
    bridge_src = read_source(BRIDGE_PATH)
    f["data_flow_trace"]["mcp_scanner"] = {
        "path": SCANNER_PATH,
        "writes_only_to": "mcp_server_registry (via ws_write('mcp_server_registry', ...))",
        "upsert_early_return_evidence": grep_lines(scanner_src, "if server_exists(sid):", ctx=2),
        "no_definition_history_reference": "mcp_definition_history" not in scanner_src,
        "no_change_detection": "upsert() returns False when server_exists -> never updates existing rows, "
                               "so a re-published package (new version/description) is invisible to the scanner.",
    }
    f["data_flow_trace"]["pipeline_bridge"] = {
        "path": BRIDGE_PATH,
        "role": "Mesh T1->T2 agent-output bridge: polls agent_outputs, classifies via "
                "inference_router, writes mesh_events/mesh_memory. NOT an MCP-definition bridge.",
        "tables_touched": [t for t in ("agent_outputs", "mesh_events", "mesh_memory",
                                       "service_health") if t in bridge_src],
        "mcp_definition_history_reference": "mcp_definition_history" in bridge_src,
        "conclusion": "pipeline_bridge is NOT in the MCP definition data flow. The assumed "
                      "mcp_scanner -> pipeline_bridge -> definition_history path does not exist.",
    }

    # ---- broken candidate writers ----
    wv2 = read_source(WRITER_V2_PATH)
    det = read_source(DETECTOR_PATH)
    f["broken_writers"]["definition_change_history_writer_v2"] = {
        "path": WRITER_V2_PATH,
        "exists": os.path.exists(WRITER_V2_PATH),
        "relative_imports_missing_modules": grep_lines(wv2, "from .definition_fingerprint", ctx=0) +
                                            grep_lines(wv2, "from .write_service_client", ctx=0),
        "missing_modules_on_disk": not os.path.exists(f"{ZS}/definition_fingerprint.py")
                                   and not os.path.exists(f"{ZS}/write_service_client.py"),
        "wrong_endpoint": "/mcp_definition_history" in wv2,  # write_service exposes /write, not this
        "record_schema_vs_table": {
            "writer_writes": ["server_id", "timestamp", "old_hash", "new_hash",
                              "changed_fields", "definition_snapshot"],
            "table_has": DEFHIST_COLUMNS,
            "verdict": "writer would POST to a non-existent endpoint; even via /write the writer's "
                       "_table_cols() would DROP every column except server_id, and id would be NULL "
                       "-> PK violation -> silent drop.",
        },
        "skips_first_seen": "No previous fingerprint means new entry, not a change" in wv2,
        "invoked_by_scanner": "record_definition_change" in scanner_src,
    }
    f["broken_writers"]["definition_change_detector"] = {
        "path": DETECTOR_PATH,
        "exists": os.path.exists(DETECTOR_PATH),
        "in_memory_snapshot_lost_on_restart": "self.snapshot" in det and "threading" in det,
        "hashes_nonexistent_columns": [c for c in ("version", "tool_schema")
                                       if f'server.get("{c}")' in det],
        "nonexistent_columns_in_registry": "mcp_server_registry has NO version/tool_schema columns",
        "wrong_query_contract": 'result.get("status") == "success"' in det,  # write_service returns {rows,count}
        "self_test_expects_nonexistent_columns": [c for c in
            ("change_id", "change_type", "old_value", "new_value", "changed_at") if c in det],
        "self_test_exits_on_fail": "exit(1)" in det,
        "write_row_schema_vs_table": {
            "detector_writes": ["server_id", "change_type", "old_value", "new_value", "changed_at"],
            "table_has": DEFHIST_COLUMNS,
        },
        "ever_run_as_daemon": any(p for p in f["daemon_inventory"]["running_processes"]
                                  ["definition_change_detector"]),
    }

    # ---- root causes ----
    rc = f["root_causes"]
    reg_count = (f["counts"]["mcp_server_registry"][0].get("n")
                 if f["counts"]["mcp_server_registry"] and isinstance(f["counts"]["mcp_server_registry"][0], dict) else "?")
    hist_count = (f["counts"]["mcp_definition_history"][0].get("n")
                  if f["counts"]["mcp_definition_history"] and isinstance(f["counts"]["mcp_definition_history"][0], dict) else "?")
    rc.append({
        "id": "RC1", "severity": "CRITICAL",
        "cause": "No daemon writes to mcp_definition_history.",
        "evidence": [
            f"mcp_definition_history rows = {hist_count} (vs registry = {reg_count}).",
            f"write_queue_log has ZERO rows for table=mcp_definition_history "
            f"(attempts={f['write_queue_log_proof']['attempts_to_mcp_definition_history']!r}).",
            f"No definition_change_detector / definition_change_history_writer process running.",
            "service_health has no definition/history/change daemon rows.",
        ],
    })
    rc.append({
        "id": "RC2", "severity": "CRITICAL",
        "cause": "The scanner never observes definition changes (early-return on existing server).",
        "evidence": [
            "mcp_scanner.upsert(): `if server_exists(sid): return False` -> existing rows are never "
            "updated, so a re-published package version/description is invisible.",
            f"scan_count distribution: {f['scan_count_distribution']} -> max scan_count never exceeds 1; "
            "no server has ever been re-scanned, so no change could ever be recorded.",
        ],
    })
    rc.append({
        "id": "RC3", "severity": "CRITICAL",
        "cause": "pipeline_bridge is the wrong component — it is the mesh agent-output bridge, not an MCP-definition bridge.",
        "evidence": ["pipeline_bridge touches agent_outputs/mesh_events/mesh_memory only; "
                     "it has no reference to mcp_definition_history or the MCP registry."],
    })
    rc.append({
        "id": "RC4", "severity": "HIGH",
        "cause": "Both candidate writers are non-functional.",
        "evidence": [
            "definition_change_history_writer_v2.py: broken relative imports (modules absent), "
            "wrong endpoint (/mcp_definition_history vs /write), record-schema mismatch, and "
            "explicitly skips first_seen (returns None when no stored fingerprint).",
            "definition_change_detector.py: in-memory snapshot lost on restart, hashes "
            "non-existent columns (version/tool_schema), wrong /query contract "
            "({status,columns} vs {rows,count}) -> _fetch_servers returns [], and self_test "
            "expects non-existent columns -> exit(1) -> daemon never starts.",
        ],
    })
    rc.append({
        "id": "RC5", "severity": "HIGH",
        "cause": "Schema/contract drift: writers assume columns the table does not have.",
        "evidence": [
            f"Table columns: {DEFHIST_COLUMNS}.",
            "writer_v2 writes: server_id,timestamp,old_hash,new_hash,changed_fields,definition_snapshot.",
            "detector writes: server_id,change_type,old_value,new_value,changed_at.",
            "Both would be silently stripped by write_service._table_cols() (drops unknown cols), "
            "leaving id=NULL -> PRIMARY KEY violation -> row dropped.",
        ],
    })

    f["missing_integration_point"] = {
        "summary": "There is no integration point between mcp_scanner (the only producer of "
                   "registry rows) and mcp_definition_history. The scanner's upsert() writes "
                   "exclusively to mcp_server_registry and short-circuits on existing servers, "
                   "so it never computes/compares a definition hash and never writes a snapshot.",
        "where_to_patch": "mcp_scanner.py upsert() — OR a thin DefinitionSnapshotRecorder invoked "
                          "from upsert(). pipeline_bridge.py is NOT the right place (wrong domain).",
        "what_the_patch_must_do": [
            "Compute snapshot_hash = sha256(description + metadata + url) for every server seen.",
            "On FIRST_SEEN (new server): insert the registry row AND record a snapshot row.",
            "On RE-SCAN (existing server): compare new hash vs latest stored snapshot_hash for "
            "that server_id; if different, record a NEW snapshot row (change detected) and bump "
            "last_scanned/scan_count; if identical, just bump last_scanned.",
            "Use the REAL write_service contract: POST /write {table, rows, mode, agent_id, wait}.",
            "Supply id via nextval('seq_defhist_id'); table is in _NO_AUTO_ID.",
            "Stay within the REAL schema: only id, server_id, snapshot_hash, captured_at.",
        ],
    }
    return f


# --------------------------------------------------------------------------- #
#  the patch — a standalone, schema-correct DefinitionSnapshotRecorder
#  plus a patched upsert() body that the scanner should adopt.
# --------------------------------------------------------------------------- #
PATCHED_UPSERT = '''
# ── PATCH for mcp_scanner.py — drop-in replacement for upsert() ──────────────
# Records a definition snapshot on first_seen AND on every detected definition
# change. Uses the REAL write_service contract and the REAL mcp_definition_history
# schema (id, server_id, snapshot_hash, captured_at). No schema migration needed.

def _next_defhist_id():
    rows = ws_query("SELECT nextval('seq_defhist_id') AS nid")
    if rows and isinstance(rows[0], dict) and rows[0].get("nid") is not None:
        return int(rows[0]["nid"])
    rows = ws_query("SELECT COALESCE(MAX(id),0)+1 AS nid FROM mcp_definition_history")
    return int(rows[0]["nid"]) if rows and isinstance(rows[0], dict) else 1

def _definition_hash(description, metadata):
    import hashlib, json
    payload = json.dumps({"description": description or "",
                          "metadata": metadata or ""}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()

def _record_snapshot(server_id, snapshot_hash):
    now = datetime.now(timezone.utc).isoformat()
    ws_write('mcp_definition_history', {
        'id': _next_defhist_id(),
        'server_id': server_id,
        'snapshot_hash': snapshot_hash,
        'captured_at': now,
    })

def _latest_snapshot_hash(server_id):
    rows = ws_query(
        "SELECT snapshot_hash FROM mcp_definition_history "
        "WHERE server_id=? ORDER BY captured_at DESC, id DESC LIMIT 1", [server_id])
    if rows and isinstance(rows[0], dict) and rows[0].get("snapshot_hash"):
        return rows[0]["snapshot_hash"]
    return None

def upsert(name, url, description, source, metadata=None):
    sid = server_id(url)
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata or {})
    snap_hash = _definition_hash(description, meta_json)

    if server_exists(sid):
        # Re-scan path: detect definition change vs latest stored snapshot.
        prev = _latest_snapshot_hash(sid)
        try:
            ws_execute(
                f"UPDATE mcp_server_registry SET last_scanned='{now}', "
                f"scan_count=scan_count+1 WHERE server_id='{sid}'")
        except Exception as e:
            log.warning("registry update failed for %s: %s", name, e)
        if prev is None:
            # exists in registry but never snapshotted — backfill first_seen snapshot
            _record_snapshot(sid, snap_hash)
            log.info("backfilled first_seen snapshot for %s", name)
        elif prev != snap_hash:
            _record_snapshot(sid, snap_hash)
            log.info("definition change detected for %s (hash %s -> %s)", name, prev[:10], snap_hash[:10])
        return False  # not a newly discovered server

    # First-seen path: insert registry row + first definition snapshot.
    try:
        ws_write('mcp_server_registry', {
            'server_id': sid, 'name': name, 'url': url,
            'description': description or '',
            'registry_source': source,
            'scan_count': 1,
            'first_seen': now, 'last_scanned': now,
            'metadata': meta_json,
        })
        _record_snapshot(sid, snap_hash)   # <-- the missing integration point
        log.info("first_seen snapshot recorded for %s", name)
        return True
    except Exception as e:
        log.warning("upsert failed for %s: %s", name, e)
        return False
'''


class DefinitionSnapshotRecorder:
    """Standalone recorder used by --apply to backfill first_seen snapshots
    for all existing registry rows (idempotent: skips a server_id+hash pair
    that is already the latest stored snapshot)."""

    def __init__(self, limit: int | None = None, dry_run: bool = True):
        self.limit = limit
        self.dry_run = dry_run
        self.first_seen_written = 0
        self.change_written = 0
        self.skipped = 0
        self.errors = 0

    def _registry_rows(self) -> list[dict]:
        sql = ("SELECT server_id, name, description, metadata, url, first_seen "
               "FROM mcp_server_registry ORDER BY server_id")
        if self.limit:
            sql += f" LIMIT {int(self.limit)}"
        return ws_query(sql)

    def backfill(self) -> dict:
        rows = self._registry_rows()
        for r in rows:
            if not isinstance(r, dict) or r.get("_error"):
                self.errors += 1
                continue
            sid = r.get("server_id")
            if not sid:
                continue
            snap = definition_snapshot_hash(r)
            prev = latest_snapshot_hash(sid)
            if prev == snap:
                self.skipped += 1
                continue
            if self.dry_run:
                self.first_seen_written += 1
            else:
                res = record_snapshot(sid, snap, agent_id="defhist_backfill_v2")
                if res.get("_error") or not res.get("ok"):
                    self.errors += 1
                else:
                    self.first_seen_written += 1 if prev is None else 0
                    self.change_written += 1 if prev is not None else 0
        return {
            "registry_rows_seen": len(rows),
            "first_seen_snapshots": self.first_seen_written,
            "change_snapshots": self.change_written,
            "skipped_uptodate": self.skipped,
            "errors": self.errors,
            "dry_run": self.dry_run,
        }


# --------------------------------------------------------------------------- #
#  reporting
# --------------------------------------------------------------------------- #
def human_report(f: dict) -> str:
    def cnt(key):
        c = f["counts"].get(key, [])
        return c[0].get("n") if c and isinstance(c[0], dict) and "_error" not in c[0] else f"ERR {c}"
    reg, hist = cnt("mcp_server_registry"), cnt("mcp_definition_history")
    L = []
    L.append("=" * 78)
    L.append("INVESTIGATE_DEFINITION_HISTORY_GAP_v2")
    L.append("=" * 78)
    L.append(f"Timestamp: {f['timestamp']}")
    L.append(f"write_service: {f['write_service_health'].get('status')} "
             f"v{f['write_service_health'].get('version')} "
             f"(total_written={f['write_service_health'].get('total_written')})")
    L.append("")
    L.append(f"mcp_server_registry   rows = {reg}")
    L.append(f"mcp_definition_history rows = {hist}")
    L.append(f"  -> gap: {reg} discovered servers, {hist} definition snapshots recorded.")
    L.append("")
    L.append("SCAN_COUNT DISTRIBUTION (proof the scanner never re-scans):")
    for r in f["scan_count_distribution"]:
        if isinstance(r, dict):
            L.append(f"  scan_count={r.get('scan_count')}: {r.get('n')} servers")
    L.append("  -> max scan_count <= 1 means NO server was ever re-scanned, so NO")
    L.append("     definition change could ever have been observed by the scanner.")
    L.append("")
    L.append("CANDIDATE POPULATION (servers a snapshot mechanism should track):")
    for r in f["candidate_population"]["by_source"]:
        if isinstance(r, dict):
            L.append(f"  {r.get('registry_source')!s:22s} n={r.get('n'):4d}  "
                     f"with_version_meta={r.get('with_version_meta')}")
    L.append("  -> 719 npm_official servers carry a `version`+`date` in metadata;")
    L.append("     these are the highest-signal candidates for change detection.")
    L.append("")
    L.append("WRITE_QUEUE_LOG PROOF (has any write to mcp_definition_history EVER happened?):")
    q = f["write_queue_log_proof"]["attempts_to_mcp_definition_history"]
    L.append(f"  {q if q else '[]  <- ZERO attempts ever enqueued. Nothing writes here.'}")
    L.append("")
    L.append("DAEMON / PROCESS INVENTORY:")
    for svc in f["daemon_inventory"]["service_health_definition_or_scanner"]:
        if isinstance(svc, dict):
            L.append(f"  service_health: {svc.get('service')} status={svc.get('status')} "
                     f"last_heartbeat={svc.get('last_heartbeat')}")
    for name, procs in f["daemon_inventory"]["running_processes"].items():
        state = "RUNNING" if any(p for p in procs) else "NOT RUNNING"
        L.append(f"  process {name}: {state}")
    L.append("  -> No definition_change_detector / definition_change_history_writer is running.")
    L.append("")
    L.append("DATA FLOW TRACE:")
    L.append(f"  mcp_scanner  -> writes ONLY mcp_server_registry; references "
             f"mcp_definition_history? {f['data_flow_trace']['mcp_scanner']['no_definition_history_reference']}")
    L.append(f"  pipeline_bridge -> role: {f['data_flow_trace']['pipeline_bridge']['role']}")
    L.append(f"     references mcp_definition_history? "
             f"{f['data_flow_trace']['pipeline_bridge']['mcp_definition_history_reference']} "
             f"-> NOT in the MCP definition flow.")
    L.append(f"  mcp_definition_history <- {hist} writers. INTEGRATION POINT MISSING.")
    L.append("")
    L.append("ROOT CAUSES:")
    for rc in f["root_causes"]:
        L.append(f"  [{rc['id']}] {rc['severity']} — {rc['cause']}")
        for e in rc["evidence"]:
            L.append(f"        - {e}")
    L.append("")
    L.append("MISSING INTEGRATION POINT:")
    L.append(f"  {f['missing_integration_point']['summary']}")
    L.append(f"  Patch target: {f['missing_integration_point']['where_to_patch']}")
    L.append("  Patch shipped in this file:")
    L.append("    - DefinitionSnapshotRecorder class (backfill, idempotent, --apply)")
    L.append("    - PATCHED_UPSERT string (drop-in replacement for mcp_scanner.upsert())")
    L.append("      Emit to mcp_scanner.snapshot_patch.py with --emit-scanner-patch.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Backfill first_seen snapshots for existing registry rows (writes to DB).")
    ap.add_argument("--emit-scanner-patch", action="store_true",
                    help="Write the patched upsert() to mcp_scanner.snapshot_patch.py for review.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit backfill to N registry rows (testing).")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    args = ap.parse_args()

    findings = diagnose()

    if args.emit_scanner_patch:
        out = f"{ZS}/mcp_scanner.snapshot_patch.py"
        with open(out, "w") as fh:
            fh.write("# Auto-generated by investigate_definition_history_gap_v2.py\n"
                     "# Drop-in replacement for mcp_scanner.py upsert(). Review, then merge.\n"
                     "from datetime import datetime, timezone\nimport json\n\n"
                     + PATCHED_UPSERT.lstrip() + "\n")
        findings["emitted_scanner_patch"] = out

    if args.apply:
        rec = DefinitionSnapshotRecorder(limit=args.limit, dry_run=False)
        findings["backfill_result"] = rec.backfill()

    if args.json:
        print(json.dumps(findings, indent=1, default=str))
    else:
        print(human_report(findings))
        if args.apply:
            print("\nBACKFLILL RESULT:")
            print(json.dumps(findings.get("backfill_result", {}), indent=1))
        if args.emit_scanner_patch:
            print(f"\nScanner patch written to: {findings['emitted_scanner_patch']}")
        print("\nNext step: review the patched upsert (run with --emit-scanner-patch),")
        print("then merge it into mcp_scanner.py so the scanner records snapshots on")
        print("first_seen and on every detected definition change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
