#!/usr/bin/env python3
"""
orphan_router_census_report.py
Generates a census report of orphan routers - MCP servers in mcp_server_registry
that have no entries in mcp_registry_facts.
"""
import sys
import os
import logging
import hashlib
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

SERVICE_NAME = 'orphan_router_census_report'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_PATH = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service (DuckDB gateway)."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL + '/query',
            json={'sql': sql},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows via write_service."""
    if not rows:
        return True
    try:
        response = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed for table {table}: {e}")
        return False


def generate_orphan_census() -> List[Dict[str, Any]]:
    """Find all orphan routers (servers with no registry facts)."""
    sql = """
    SELECT 
        r.server_id,
        r.name,
        r.url,
        r.description,
        r.trust_score,
        r.verdict,
        r.registry_source,
        r.scan_count,
        r.first_seen,
        r.last_scanned,
        r.last_assessed
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_registry_facts f 
        WHERE f.server_id = r.server_id
    )
    ORDER BY r.scan_count DESC NULLS LAST, r.name ASC
    """
    return ws_query(sql)


def compute_census_metrics(orphans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics for the orphan census."""
    total = len(orphans)
    if total == 0:
        return {
            'total_orphans': 0,
            'by_verdict': {},
            'by_source': {},
            'avg_trust_score': 0.0,
            'median_scan_count': 0,
            'most_scanned': None,
            'oldest_first_seen': None,
            'newest_first_seen': None
        }
    
    by_verdict: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    trust_scores: List[float] = []
    scan_counts: List[int] = []
    
    for orphan in orphans:
        verdict = orphan.get('verdict', 'UNKNOWN')
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        
        source = orphan.get('registry_source', 'UNKNOWN')
        by_source[source] = by_source.get(source, 0) + 1
        
        if orphan.get('trust_score') is not None:
            trust_scores.append(float(orphan['trust_score']))
        
        if orphan.get('scan_count') is not None:
            scan_counts.append(int(orphan['scan_count']))
    
    avg_trust = sum(trust_scores) / len(trust_scores) if trust_scores else 0.0
    median_scans = sorted(scan_counts)[len(scan_counts) // 2] if scan_counts else 0
    most_scanned = max(orphans, key=lambda x: x.get('scan_count') or 0) if orphans else None
    first_seens = [o['first_seen'] for o in orphans if o.get('first_seen')]
    
    return {
        'total_orphans': total,
        'by_verdict': by_verdict,
        'by_source': by_source,
        'avg_trust_score': round(avg_trust, 2),
        'median_scan_count': median_scans,
        'most_scanned': most_scanned['name'] if most_scanned else None,
        'oldest_first_seen': min(first_seens) if first_seens else None,
        'newest_first_seen': max(first_seens) if first_seens else None
    }


def write_census_report(report: Dict[str, Any]) -> bool:
    """Write census report to census_reports table."""
    rows = [{
        'report_id': hashlib.md5(
            (report['generated_at'] + '_orphan_router_census').encode()
        ).hexdigest(),
        'report_type': 'orphan_router_census',
        'generated_at': report['generated_at'],
        'total_orphans': report['metrics']['total_orphans'],
        'avg_trust_score': report['metrics']['avg_trust_score'],
        'median_scan_count': report['metrics']['median_scan_count'],
        'most_scanned_name': report['metrics']['most_scanned'],
        'by_verdict_json': str(report['metrics']['by_verdict']),
        'by_source_json': str(report['metrics']['by_source']),
        'oldest_first_seen': report['metrics']['oldest_first_seen'],
        'newest_first_seen': report['metrics']['newest_first_seen'],
        'sample_server_ids': str([s['server_id'] for s in report['orphans'][:100]])
    }]
    return ws_write('census_reports', rows)


def format_markdown_report(report: Dict[str, Any]) -> str:
    """Format census report as markdown for logging/debugging."""
    m = report['metrics']
    lines = [
        f"# Orphan Router Census Report",
        f"Generated: {report['generated_at']}",
        "",
        f"## Summary",
        f"- Total orphan routers: {m['total_orphans']}",
        f"- Average trust score: {m['avg_trust_score']}",
        f"- Median scan count: {m['median_scan_count']}",
        f"- Most scanned orphan: {m['most_scanned'] or 'N/A'}",
        "",
        f"## By Verdict"
    ]
    
    for verdict, count in sorted(m['by_verdict'].items(), key=lambda x: -x[1]):
        lines.append(f"  - {verdict}: {count}")
    
    lines.extend(["", "## By Source"])
    for source, count in sorted(m['by_source'].items(), key=lambda x: -x[1]):
        lines.append(f"  - {source}: {count}")
    
    lines.extend(["", "## Top 20 Sample Orphans", "| Name | URL | Verdict | Trust | Scans |", "|------|-----|---------|-------|-------|"])
    
    for orphan in report['orphans'][:20]:
        name = (orphan.get('name') or 'N/A')[:50]
        url = (orphan.get('url') or 'N/A')[:50]
        verdict = orphan.get('verdict', 'UNKNOWN')
        trust = orphan.get('trust_score', 'N/A')
        scans = orphan.get('scan_count', 0)
        lines.append(f"| {name} | {url} | {verdict} | {trust} | {scans} |")
    
    return "\n".join(lines)


def run():
    """Run one cycle of orphan router census."""
    logger.info("Starting orphan router census report generation")
    generated_at = datetime.now(timezone.utc).isoformat()
    
    orphans = generate_orphan_census()
    logger.info(f"Found {len(orphans)} orphan routers")
    
    metrics = compute_census_metrics(orphans)
    logger.info(f"Metrics computed: {metrics}")
    
    report = {
        'generated_at': generated_at,
        'orphans': orphans,
        'metrics': metrics
    }
    
    md_report = format_markdown_report(report)
    logger.info(f"\n{md_report}")
    
    write_success = write_census_report(report)
    if write_success:
        logger.info("Census report written to census_reports table")
    else:
        logger.warning("Failed to write census report to database")
    
    return report


if __name__ == '__main__':
    try:
        result = run()
        total = result['metrics']['total_orphans']
        logger.info(f"Orphan router census complete. Found {total} orphans.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Orphan router census failed: {e}", exc_info=True)
        sys.exit(1)