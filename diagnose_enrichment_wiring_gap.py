#!/usr/bin/env python3
"""
diagnose_enrichment_wiring_gap.py

Investigates why mcp_signal_enrichments has only 12 rows while mcp_signal_scores
has ~2.1M rows. The gap indicates enrichment modules exist but are either:
1. Not being called by signal_analyser, or
2. Failing before write, or
3. Wired incorrectly (schema mismatch / broken queries)

Checks performed:
- signal_analyser.py: get_enrichments() defined but called?
- mcp_signal_scores schema: enrichment_type column exists?
- mcp_signal_enrichments_writer_daemon query: valid?
- Live row counts and sample data
"""

import sys
import json
import os
import requests

WRITE_SERVICE = 'http://127.0.0.1:8772'
WRITE_URL = f'{WRITE_SERVICE}/write'
QUERY_URL = f'{WRITE_SERVICE}/query'
EXECUTE_URL = f'{WRITE_SERVICE}/execute'
HTTP_TIMEOUT = 10

sys.path.insert(0, '/home/workspace/zo_sentinel')


def ws_query(sql: str) -> list:
    """Query write_service."""
    payload = {'sql': sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get('rows', [])
        print(f"Query failed {resp.status_code}: {resp.text[:300]}")
        return []
    except Exception as e:
        print(f"Query exception: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    """Write to write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=HTTP_TIMEOUT)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"Write exception: {e}")
        return False


def print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def main():
    findings = []
    warnings = []

    # ── 1. Row counts ──────────────────────────────────────────────
    print_section("1. ROW COUNTS")
    scores_count = ws_query("SELECT COUNT(*) as cnt FROM mcp_signal_scores")
    enrichments_count = ws_query("SELECT COUNT(*) as cnt FROM mcp_signal_enrichments")
    print(f"mcp_signal_scores rows:        {scores_count[0]['cnt'] if scores_count else 'N/A'}")
    print(f"mcp_signal_enrichments rows:  {enrichments_count[0]['cnt'] if enrichments_count else 'N/A'}")

    if scores_count and enrichments_count:
        ratio = enrichments_count[0]['cnt'] / max(scores_count[0]['cnt'], 1)
        print(f"Ratio (enrichments/scores):   {ratio:.8f}  (expected ~0.2-1.0 for full coverage)")
        if ratio < 0.0001:
            findings.append(f"CRITICAL: Only {enrichments_count[0]['cnt']} enrichments vs {scores_count[0]['cnt']} scores — wiring is broken")

    # ── 2. mcp_signal_scores schema ────────────────────────────────
    print_section("2. mcp_signal_scores SCHEMA")
    cols = ws_query("PRAGMA table_info(mcp_signal_scores)")
    if not cols:
        cols = ws_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='mcp_signal_scores'")
    print("Columns:")
    col_names = []
    for col in cols:
        name = col.get('name') or col.get('column_name') or '?'
        dtype = col.get('type') or col.get('data_type') or '?'
        print(f"  {name}: {dtype}")
        col_names.append(name.lower())

    has_enrichment_type = 'enrichment_type' in col_names
    print(f"\nHas 'enrichment_type' column: {has_enrichment_type}")
    if not has_enrichment_type:
        findings.append("CRITICAL: mcp_signal_scores has NO 'enrichment_type' column — mcp_signal_enrichments_writer_daemon's query always returns 0 rows")

    # ── 3. mcp_signal_enrichments schema ───────────────────────────
    print_section("3. mcp_signal_enrichments SCHEMA")
    enrich_cols = ws_query("PRAGMA table_info(mcp_signal_enrichments)")
    if not enrich_cols:
        enrich_cols = ws_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='mcp_signal_enrichments'")
    print("Columns:")
    for col in enrich_cols:
        print(f"  {col.get('name') or col.get('column_name')}: {col.get('type') or col.get('data_type')}")

    # ── 4. Sample enrichments ───────────────────────────────────────
    print_section("4. SAMPLE mcp_signal_enrichments ROWS")
    samples = ws_query("SELECT * FROM mcp_signal_enrichments LIMIT 5")
    if samples:
        for row in samples:
            print(json.dumps(row, indent=2, default=str))
    else:
        print("  (no rows)")

    # ── 5. signal_analyser.py — get_enrichments() wiring ───────────
    print_section("5. signal_analyser.py — get_enrichments() WIRING")
    sa_path = '/home/workspace/zo_sentinel/signal_analyser.py'
    if os.path.exists(sa_path):
        with open(sa_path) as f:
            sa_src = f.read()

        has_def = 'def get_enrichments(' in sa_src
        calls_anywhere = sa_src.count('get_enrichments(')
        print(f"def get_enrichments() defined: {has_def}")
        print(f"get_enrichments( called {calls_anywhere} time(s) in file")

        # Extract process_server body
        if 'def process_server(' in sa_src:
            ps_start = sa_src.find('def process_server(')
            ps_end = sa_src.find('\ndef ', ps_start + 1)
            ps_body = sa_src[ps_start:ps_end] if ps_end > 0 else sa_src[ps_start:]
            ps_calls_enrichments = 'get_enrichments(' in ps_body
            ps_writes_enrichments = 'mcp_signal_enrichments' in ps_body
            print(f"process_server() calls get_enrichments(): {ps_calls_enrichments}")
            print(f"process_server() writes to mcp_signal_enrichments: {ps_writes_enrichments}")

            if not ps_calls_enrichments and not ps_writes_enrichments:
                findings.append("CRITICAL: get_enrichments() is defined but NEVER called in process_server() — enrichments computed but never written")
            elif ps_calls_enrichments and not ps_writes_enrichments:
                warnings.append("process_server() calls get_enrichments() but doesn't write results to mcp_signal_enrichments")
    else:
        print(f"  signal_analyser.py not found at {sa_path}")
        findings.append(f"signal_analyser.py not found at {sa_path}")

    # ── 6. mcp_signal_enrichments_writer_daemon.py query validity ──
    print_section("6. mcp_signal_enrichments_writer_daemon.py QUERY VALIDITY")
    daemon_path = '/home/workspace/zo_sentinel/mcp_signal_enrichments_writer_daemon.py'
    if os.path.exists(daemon_path):
        with open(daemon_path) as f:
            daemon_src = f.read()

        refs_enrichment_type = 'enrichment_type' in daemon_src
        print(f"Daemon references 'enrichment_type': {refs_enrichment_type}")

        if refs_enrichment_type:
            # Show the query
            if 'def _build_pending_query(' in daemon_src:
                q_start = daemon_src.find('def _build_pending_query')
                q_end = daemon_src.find('\n    def ', q_start + 1)
                q_body = daemon_src[q_start:q_end] if q_end > 0 else daemon_src[q_start:]
                print("\nDaemon query snippet:")
                for line in q_body.split('\n')[:20]:
                    print(f"  {line}")
            findings.append("CRITICAL: Daemon queries 'enrichment_type' column which does NOT exist in mcp_signal_scores — query returns 0 rows every cycle")
    else:
        print(f"  Daemon not found at {daemon_path}")

    # ── 7. Direct enrichment writers ──────────────────────────────
    print_section("7. DIRECT WRITERS TO mcp_signal_enrichments")
    writers = {
        'supply_chain_enrichment_wiring.py': 'supply_chain',
        'temporal_stability_enrichment_v2.py': 'temporal_stability',
        'tool_description_safety_enrichment_v3.py': 'tool_description_safety',
        'permission_scope_enrichment_integration.py': 'permission_scope',
        'community_signal_enrichment_wiring.py': 'community_signal',
        'domain_trust_enrichment_wiring.py': 'domain_trust',
        'injection_resilience_wiring.py': 'injection_resilience',
        'stale_signal_refresher.py': 'stale_signal',
    }
    active_writers = []
    for fname, sig_name in writers.items():
        fpath = f'/home/workspace/zo_sentinel/{fname}'
        if os.path.exists(fpath):
            with open(fpath) as f:
                src = f.read()
            writes_enrichments = 'mcp_signal_enrichments' in src and 'ws_write' in src
            status = 'WRITES' if writes_enrichments else 'present (no write)'
            print(f"  {fname}: {status}")
            if writes_enrichments:
                active_writers.append(fname)
        else:
            print(f"  {fname}: NOT FOUND")

    print(f"\n  Active direct writers: {len(active_writers)}")
    if len(active_writers) == 0:
        findings.append("No active direct writers found — mcp_signal_enrichments has no ingestion path")

    # ── 8. service_health: which daemons are running ───────────────
    print_section("8. DAEMON HEALTH STATUS")
    health = ws_query("SELECT service, last_heartbeat FROM service_health WHERE service LIKE '%enrichment%' OR service LIKE '%signal%' ORDER BY service")
    if health:
        for row in health:
            print(f"  {row.get('service')}: {row.get('last_heartbeat')}")
    else:
        print("  No enrichment/signal daemons found in service_health")

    # ── 9. Check signal_analyser process_server end-to-end ────────
    print_section("9. process_server() FULL TRACE")
    if os.path.exists(sa_path):
        with open(sa_path) as f:
            sa_src = f.read()
        if 'def process_server(' in sa_src:
            ps_start = sa_src.find('def process_server(')
            ps_end = sa_src.find('\ndef ', ps_start + 1)
            ps_body = sa_src[ps_start:ps_end] if ps_end > 0 else sa_src[ps_start:]
            print("Key operations in process_server():")
            for op in ['ws_write', 'get_enrichments', 'mcp_signal_enrichments', 'mcp_signal_scores',
                       'compute_url_safety', 'compute_tool_security', 'compute_supply_chain',
                       'compute_reputation', 'compute_domain_trust', 'compute_composite']:
                count = ps_body.count(op)
                present = 'YES' if count > 0 else ' NO'
                print(f"  {op}: {present} ({count}x)")

    # ── 10. Summary ─────────────────────────────────────────────────
    print_section("10. SUMMARY — ROOT CAUSES")
    if not findings:
        print("No critical issues found")
    else:
        print(f"Found {len(findings)} critical issue(s):")
        for i, finding in enumerate(findings, 1):
            print(f"  {i}. {finding}")

    if warnings:
        print(f"\nFound {len(warnings)} warning(s):")
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}")

    print(f"\n{'='*60}")
    print(f"  mcp_signal_enrichments count: {enrichments_count[0]['cnt'] if enrichments_count else 'N/A'}")
    print(f"  mcp_signal_scores count:      {scores_count[0]['cnt'] if scores_count else 'N/A'}")
    print('='*60)

    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())