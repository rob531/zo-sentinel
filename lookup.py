#!/usr/bin/env python3
"""
lookup.py -- ZO-SENTINEL CLI lookup tool for MCP server intelligence.
Usage: python3 lookup.py <mcp_name_or_url> [options]
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8773/execute"

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"


def ws_query(sql, params=None):
    """Execute SQL query against DuckDB via inference_router."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows, wait=True):
    """Write rows to DuckDB via write_service."""
    url = f"{WRITE_SERVICE_URL}/write"
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def color_for_risk(risk_level):
    """Return ANSI color code for risk level."""
    if risk_level and risk_level.upper() in ("HIGH", "HIGH_RISK", "CRITICAL"):
        return ANSI_RED
    if risk_level and risk_level.upper() in ("MEDIUM", "CAUTION"):
        return ANSI_YELLOW
    if risk_level and risk_level.upper() in ("LOW", "TRUSTED", "APPROVED"):
        return ANSI_GREEN
    return ANSI_YELLOW


def color_for_verdict(verdict):
    """Return ANSI color code for verdict."""
    if verdict and verdict.upper() in ("APPROVED", "TRUSTED"):
        return ANSI_GREEN
    if verdict and verdict.upper() in ("APPROVED_WITH_CONDITIONS",):
        return ANSI_YELLOW
    if verdict and verdict.upper() in ("DENIED", "RUG_PULL_ALERT"):
        return ANSI_RED
    return ANSI_YELLOW


def format_trust_bar(score, width=20):
    """Create ASCII trust score bar."""
    if score is None:
        return f"[{ANSI_DIM}{'?' * width}{ANSI_RESET}]"
    filled = int(score * width)
    empty = width - filled
    color = color_for_risk("HIGH_RISK" if score < 0.4 else "MEDIUM" if score < 0.7 else "LOW")
    return f"[{color}{'█' * filled}{ANSI_RESET}{ANSI_DIM}{'░' * empty}{ANSI_RESET}]"


def query_registry(name_or_url):
    """Query mcp_server_registry for server info."""
    sql = """
    SELECT server_id, name, registry_source, url, description, trust_score,
           verdict, verdict_reasoning, confidence, last_assessed, first_seen, last_seen, scan_count
    FROM mcp_server_registry
    WHERE name ILIKE ? OR server_id ILIKE ? OR url ILIKE ?
    ORDER BY last_seen DESC
    LIMIT 1
    """
    pattern = f"%{name_or_url}%"
    result = ws_query(sql, [pattern, pattern, pattern])
    return result.get("rows", result.get("data", []))


def query_signal_scores(server_id):
    """Query mcp_signal_scores for signal information."""
    sql = """
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY score DESC, scored_at DESC
    LIMIT 10
    """
    result = ws_query(sql, [server_id])
    return result.get("rows", result.get("data", []))


def query_threat_associations(server_id):
    """Query mcp_threat_associations for threat information."""
    sql = """
    SELECT threat_type, evidence, severity, reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    LIMIT 20
    """
    result = ws_query(sql, [server_id])
    return result.get("rows", result.get("data", []))


def query_definition_history(server_id):
    """Query mcp_definition_history for definition snapshots."""
    sql = """
    SELECT snapshot_hash, snapshot_content, captured_at
    FROM mcp_definition_history
    WHERE server_id = ?
    ORDER BY captured_at DESC
    LIMIT 1
    """
    result = ws_query(sql, [server_id])
    return result.get("rows", result.get("data", []))


def query_attestation(server_id):
    """Query attestation records."""
    try:
        sql = """
        SELECT * FROM mcp_attestations
        WHERE server_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """
        result = ws_query(sql, [server_id])
        return result.get("rows", result.get("data", []))
    except Exception:
        return []


def query_risk_register(server_id):
    """Query risk register for risk information."""
    try:
        # The risk register on the bus is `mcp_risk_register`, and these are its
        # real columns. The old shape (risk_id / risk_title / risk_level /
        # mitigation_status / owner / review_date / related_servers /
        # created_at) named a table and a schema that exist on no plane, so
        # ws_query raised and this function returned [] on every call. Refs #4080.
        sql = """
        SELECT server_id, name, risk_tier, risk_rank, threat_count,
               environment_exposure, staleness_days, computed_at
        FROM mcp_risk_register
        WHERE server_id = ?
        ORDER BY risk_rank ASC, computed_at DESC
        LIMIT 5
        """
        result = ws_query(sql, [server_id])
        return result.get("rows", result.get("data", []))
    except Exception:
        return []


def lookup(name_or_url, show_threats=False, show_risks=False, show_stats=False, output_json=False, show_all=False):
    """Perform comprehensive lookup of MCP server."""
    registry_data = query_registry(name_or_url)

    if not registry_data or (isinstance(registry_data, list) and len(registry_data) == 0):
        if output_json:
            print(json.dumps({"error": "No data found", "query": name_or_url}))
        else:
            print(f"{ANSI_YELLOW}No registry data found for: {name_or_url}{ANSI_RESET}")
        return None

    if isinstance(registry_data, dict) and "columns" in registry_data:
        columns = registry_data["columns"]
        rows = registry_data.get("rows", registry_data.get("data", []))
        if rows:
            row = rows[0]
            server = dict(zip(columns, row))
        else:
            server = {}
    elif isinstance(registry_data, list) and len(registry_data) > 0:
        if isinstance(registry_data[0], dict):
            server = registry_data[0]
        else:
            server = {}
    else:
        server = registry_data if isinstance(registry_data, dict) else {}

    server_id = server.get("server_id", "")
    signals = query_signal_scores(server_id) if server_id else []
    threats = query_threat_associations(server_id) if server_id else []
    risk_entries = query_risk_register(server_id) if server_id else []
    attestation = query_attestation(server_id) if server_id else []

    if output_json:
        output = {
            "query": name_or_url,
            "server": server,
            "signal_scores": signals,
            "threat_associations": threats,
            "risk_register": risk_entries,
            "attestation": attestation
        }
        print(json.dumps(output, indent=2, default=str))
        return output

    print(f"\n{ANSI_BOLD}{'=' * 60}{ANSI_RESET}")
    name = server.get("name", server_id or name_or_url)
    verdict = server.get("verdict", "PENDING_REVIEW")
    trust_score = server.get("trust_score")

    verdict_color = color_for_verdict(verdict)
    print(f"{ANSI_BOLD}  {name}{ANSI_RESET}")
    print(f"  Verdict: {verdict_color}{verdict or 'UNKNOWN'}{ANSI_RESET}")
    print(f"  Trust: {format_trust_bar(trust_score)} {trust_score * 100:.1f}%" if trust_score is not None else "  Trust: [No Score]")

    if server.get("url"):
        print(f"  URL: {ANSI_DIM}{server['url']}{ANSI_RESET}")
    if server.get("registry_source"):
        print(f"  Source: {ANSI_DIM}{server['registry_source']}{ANSI_RESET}")

    last_seen = server.get("last_seen")
    if last_seen:
        print(f"  Last Seen: {ANSI_DIM}{last_seen}{ANSI_RESET}")

    confidence = server.get("confidence")
    if confidence is not None:
        print(f"  Confidence: {confidence * 100:.0f}%")

    print(f"{ANSI_BOLD}{'=' * 60}{ANSI_RESET}")

    if server.get("verdict_reasoning"):
        reasoning = server["verdict_reasoning"]
        if len(reasoning) > 200:
            reasoning = reasoning[:200] + "..."
        print(f"\n{ANSI_BOLD}Verdict Reasoning:{ANSI_RESET}\n  {reasoning}")

    if show_stats or show_all or signals:
        print(f"\n{ANSI_BOLD}--- Signal Scores ({len(signals) if signals else 0}) ---{ANSI_RESET}")
        if signals:
            signal_columns = ["signal_name", "score", "evidence", "scored_at"]
            if isinstance(signals, dict) and "columns" in signals:
                signal_columns = signals["columns"]
                signal_rows = signals.get("rows", signals.get("data", []))
                signals = [dict(zip(signal_columns, r)) for r in signal_rows] if signal_rows else []

            for i, sig in enumerate(signals[:3]):
                if isinstance(sig, dict):
                    sig_name = sig.get("signal_name", "unknown")
                    sig_score = sig.get("score", 0)
                    sig_evidence = sig.get("evidence", "")
                    sig_color = color_for_risk("HIGH_RISK" if sig_score < 0.4 else "MEDIUM" if sig_score < 0.7 else "LOW")
                    print(f"  {i+1}. {sig_name}: {sig_color}{sig_score:.2f}{ANSI_RESET}")
                    if sig_evidence and len(sig_evidence) < 100:
                        print(f"     {ANSI_DIM}{sig_evidence}{ANSI_RESET}")
        else:
            print(f"  {ANSI_DIM}No signal scores available{ANSI_RESET}")

    if show_threats or show_all or threats:
        threat_count = len(threats) if threats else 0
        threat_color = ANSI_RED if threat_count > 0 else ANSI_GREEN
        print(f"\n{ANSI_BOLD}--- Threat Associations ({threat_count}) ---{ANSI_RESET}")
        if threats:
            threat_columns = ["threat_type", "evidence", "severity", "reported_at"]
            if isinstance(threats, dict) and "columns" in threats:
                threat_columns = threats["columns"]
                threat_rows = threats.get("rows", threats.get("data", []))
                threats = [dict(zip(threat_columns, r)) for r in threat_rows] if threat_rows else []

            for threat in threats[:5]:
                if isinstance(threat, dict):
                    t_type = threat.get("threat_type", "unknown")
                    t_severity = threat.get("severity", "UNKNOWN")
                    t_evidence = threat.get("evidence", "")
                    t_color = color_for_risk(t_severity)
                    print(f"  • {t_color}{t_type}{ANSI_RESET} ({t_severity})")
                    if t_evidence:
                        ev = t_evidence[:80] + "..." if len(t_evidence) > 80 else t_evidence
                        print(f"    {ANSI_DIM}{ev}{ANSI_RESET}")
        else:
            print(f"  {ANSI_GREEN}No threats detected{ANSI_RESET}")

    if show_risks or show_all or risk_entries:
        print(f"\n{ANSI_BOLD}--- Risk Register ({len(risk_entries) if risk_entries else 0}) ---{ANSI_RESET}")
        if risk_entries:
            risk_columns = ["risk_id", "risk_title", "risk_level", "mitigation_status"]
            if isinstance(risk_entries, dict) and "columns" in risk_entries:
                risk_columns = risk_entries["columns"]
                risk_rows = risk_entries.get("rows", risk_entries.get("data", []))
                risk_entries = [dict(zip(risk_columns, r)) for r in risk_rows] if risk_rows else []

            for risk in risk_entries[:3]:
                if isinstance(risk, dict):
                    r_id = risk.get("risk_id", "N/A")
                    r_title = risk.get("risk_title", "Untitled Risk")
                    r_level = risk.get("risk_level", "UNKNOWN")
                    r_status = risk.get("mitigation_status", "UNKNOWN")
                    r_color = color_for_risk(r_level)
                    print(f"  [{r_id}] {r_color}{r_level}{ANSI_RESET}: {r_title}")
                    print(f"     Status: {r_status}")
        else:
            print(f"  {ANSI_GREEN}No risk entries{ANSI_RESET}")

    if attestation:
        print(f"\n{ANSI_BOLD}--- Latest Attestation ---{ANSI_RESET}")
        att_columns = None
        att_data = attestation
        if isinstance(attestation, dict) and "columns" in attestation:
            att_columns = attestation["columns"]
            att_data = attestation.get("rows", attestation.get("data", []))
        if att_data and (isinstance(att_data, list) and len(att_data) > 0):
            att = att_data[0] if isinstance(att_data, list) else att_data
            if att_columns and isinstance(att, (list, tuple)):
                att = dict(zip(att_columns, att))
            if isinstance(att, dict):
                created = att.get("created_at", att.get("scored_at", "N/A"))
                attestor = att.get("attestor", att.get("created_by", "System"))
                print(f"  Attestor: {attestor}")
                print(f"  Created: {created}")
                summary = att.get("attestation_summary", att.get("summary", ""))
                if summary:
                    s = summary[:150] + "..." if len(summary) > 150 else summary
                    print(f"  Summary: {s}")

    print(f"\n{ANSI_BOLD}{'=' * 60}{ANSI_RESET}\n")

    return server


def main():
    parser = argparse.ArgumentParser(
        description="ZO-SENTINEL CLI lookup for MCP server intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 lookup.py claude-code
  python3 lookup.py https://registry.example.com/server
  python3 lookup.py claude-code --threats --risks
  python3 lookup.py claude-code --all --json
  python3 lookup.py claude-code --stats
        """
    )
    parser.add_argument("name_or_url", help="MCP server name or URL to look up")
    parser.add_argument("--threats", action="store_true", help="Show threat associations")
    parser.add_argument("--risks", action="store_true", help="Show risk register entries")
    parser.add_argument("--stats", action="store_true", help="Show signal scores table")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--all", action="store_true", help="Show everything")

    args = parser.parse_args()

    try:
        lookup(
            args.name_or_url,
            show_threats=args.threats,
            show_risks=args.risks,
            show_stats=args.stats,
            output_json=args.json,
            show_all=args.all
        )
    except requests.exceptions.ConnectionError as e:
        print(f"{ANSI_RED}Error: Could not connect to ZO-SENTINEL services.{ANSI_RESET}", file=sys.stderr)
        print(f"{ANSI_RED}Ensure write_service (8772) and inference_router (8773) are running.{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"{ANSI_RED}HTTP Error: {e}{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"{ANSI_RED}Unexpected error: {e}{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()