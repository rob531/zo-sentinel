#!/usr/bin/env python3
"""
attestation_engine.py -- ZO-SENTINEL Phase 4: Attestation Engine
Generates formal attestations for MCP servers based on trust synthesis data.
Writes to mcp_attestations table and generates ATTESTATION_REPORT.md.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import requests
import singleton_lock  # identity-verified single-instance lock

# The paginating read door (zo_sentinel/bus.py, PR #5877). #4003 counts "583
# unbounded row reads across 313 files"; bus.py's own docstring states the cure:
# a caller swaps this module's ws_query BODY for query_complete() and every
# statement in the module is cured at once, including ones added later.
#
# Imported defensively ON PURPOSE: this file is a daemon started by
# daemon_wrapper.sh and a missing package must not take the service down. When
# the door is absent the fallback in ws_query still REFUSES to hand back a
# silently capped page -- unknown is not a complete answer (R6).
try:
    from zo_sentinel.bus import BusTruncated as _BusTruncated
    from zo_sentinel.bus import query_complete as _bus_query_complete
except Exception:  # noqa: BLE001 -- absence is a degraded mode, not a crash
    _bus_query_complete = None

    class _BusTruncated(RuntimeError):
        """Stand-in used only when zo_sentinel.bus is not importable."""

# Configuration
SERVICE_NAME = 'attestation_engine'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 21600  # 6 hours
# Env-overridable so a test (or a second checkout) never writes into the live
# service's log and report paths. The defaults are unchanged for the daemon.
LOG_DIR = os.environ.get('ZO_ATTESTATION_LOG_DIR',
                         '/home/workspace/zo_sentinel/logs')
REPORT_PATH = os.environ.get('ZO_ATTESTATION_REPORT_PATH',
                             '/home/workspace/zo_sentinel/ATTESTATION_REPORT.md')

# Verdict to expiry mapping (days)
VERDICT_EXPIRY = {
    'TRUSTED_GENERAL': 90,
    'TRUSTED_RESEARCH': 60,
    'ENTERPRISE_CONTROLLED': 60,
    'CAUTION_LIMITED': 30,
    'HIGH_RISK_ISOLATED': 7,
    'KNOWN_THREAT': 7,
    'INSUFFICIENT': 14
}

# Verdict descriptions
VERDICT_DESCRIPTIONS = {
    'TRUSTED_GENERAL': 'Likely safe for enterprise use under formal security controls',
    'TRUSTED_RESEARCH': 'Suitable for research and development environments',
    'ENTERPRISE_CONTROLLED': 'Approved for controlled use with appropriate governance',
    'CAUTION_LIMITED': 'Use with caution in isolated, non-production environments',
    'CAUTION_ELEVATED': 'Elevated risk indicators require additional monitoring',
    'HIGH_RISK_ISOLATED': 'High risk profile - use only in isolated contexts without sensitive data',
    'KNOWN_THREAT': 'Do not deploy - known threat indicators present',
    'INSUFFICIENT': 'Insufficient data for determination - requires manual review'
}

# Logging setup.
#
# A FileHandler on an absolute host path, built at IMPORT time, is why this
# module had no unit test for its entire life: importing it off the tower
# raised before any test could run, so the FLOAT/VARCHAR write failure below
# was only ever observable in a log file on one machine. The file handler is
# now best-effort and the stream handler always attaches, so the module is
# importable anywhere -- and a daemon whose log directory went read-only keeps
# running and keeps reporting instead of dying at import.
_log_handlers = [logging.StreamHandler()]
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_handlers.insert(0, logging.FileHandler(f'{LOG_DIR}/attestation_engine.log'))
except OSError as _log_exc:  # unwritable path -- stderr still carries the log
    print(f"attestation_engine: file logging disabled ({_log_exc})", file=sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger(SERVICE_NAME)


def get_write_url():
    return WRITE_SERVICE_URL


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        requests.post(WRITE_SERVICE_URL, json={
            'table': 'service_health',
            'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()},
            'wait': True
        })
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def ws_query(sql: str, params: list = None) -> list:
    """Execute SELECT via the paginating bus door. Returns EVERY row.

    Until 2026-10-04 this was one POST whose result the server caps at 200 rows
    with no caller able to notice. `get_all_servers_needing_attestation()`
    therefore asked "which servers in the registry need attesting" and was
    handed 200 of 3630 -- measured live 2026-10-04 on :8772,
    `count=200 truncated=true limit=200` against a derived `count(*)` of 3630,
    i.e. 5.5% of the fleet. See #4003.
    """
    if _bus_query_complete is not None:
        return _bus_query_complete(sql, params=params, url=QUERY_URL, timeout=30)

    # Fallback: zo_sentinel.bus is not importable. Still one request, but a
    # capped page RAISES instead of being returned as though it were the whole
    # answer. R6 -- unknown is not zero, and a short page is not completeness.
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for _flag in ('truncated', 'is_truncated', 'row_limit_hit'):
            if body.get(_flag):
                raise _BusTruncated(
                    'the bus declared this result truncated and zo_sentinel.bus '
                    'is not importable, so it cannot be paged here: ' + sql[:160]
                )
        if 'rows' in body:
            return body['rows']
        if 'results' in body:
            return body['results']
    return []


CONFIDENCE_BANDS = (('HIGH', 0.85), ('MEDIUM', 0.60))

_NUMERIC_TYPES = ('FLOAT', 'DOUBLE', 'REAL', 'DECIMAL', 'NUMERIC',
                  'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'HUGEINT')

_COLUMN_TYPES: Dict[str, Dict[str, str]] = {}


def confidence_band_of(confidence) -> str:
    """The human band. DERIVED from the number, never stored instead of it."""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return 'LOW'
    for name, floor in CONFIDENCE_BANDS:
        if value >= floor:
            return name
    return 'LOW'


def column_types(table: str) -> Dict[str, str]:
    """Live column types for `table`, resolved from information_schema.

    R1: the schema that matters is the one write_service writes into, not the
    CREATE TABLE in this file -- which has never matched it. Cached per
    process. An empty dict means UNKNOWN (R6), not "no such column", and is
    deliberately NOT cached so a later cycle can resolve it.
    """
    cached = _COLUMN_TYPES.get(table)
    if cached:
        return cached
    types: Dict[str, str] = {}
    try:
        rows = ws_query(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ?", [table])
        for row in rows or []:
            name = row.get('column_name')
            if name:
                types[str(name)] = str(row.get('data_type') or '').upper()
    except Exception as exc:  # noqa: BLE001 -- UNKNOWN, reported not swallowed
        logger.error(f"Could not resolve live column types for {table}: {exc}")
        return {}
    if types:
        _COLUMN_TYPES[table] = types
    return types


def confidence_level_for_column(confidence, band: str):
    """What `mcp_attestations.confidence_level` will actually accept.

    Numeric live column -> the number. VARCHAR live column -> the band.
    Type UNKNOWN -> the number, which is the type measured live on 2026-10-04;
    a VARCHAR column accepts the band and nothing else, so the branch is chosen
    by the live type rather than by this file's belief about it.
    """
    declared = column_types('mcp_attestations').get('confidence_level', '')
    if declared and not any(t in declared for t in _NUMERIC_TYPES):
        return band
    try:
        return float(confidence)
    except (TypeError, ValueError):
        return 0.0


def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> dict:
    """Write to DuckDB via write_service."""
    url = WRITE_SERVICE_URL  # already ends in /write
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def fetch_server_data(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch trust_score, verdict, confidence from mcp_server_registry."""
    sql = """
    SELECT server_id, name, trust_score, verdict, verdict_reasoning, confidence, risk_tier, last_assessed
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    results = ws_query(sql, [server_id])
    if results and len(results) > 0:
        return results[0]
    return None


def fetch_risk_tier(server_id: str) -> Optional[str]:
    """Fetch risk_tier from mcp_risk_register if available."""
    try:
        sql = """
        SELECT risk_tier
        FROM mcp_risk_register
        WHERE server_id = ?
        ORDER BY assessed_at DESC
        LIMIT 1
        """
        results = ws_query(sql, [server_id])
        if results and len(results) > 0:
            return results[0].get('risk_tier')
    except Exception as e:
        logger.debug(f"No risk_tier found in mcp_risk_register for {server_id}: {e}")
    return None


def build_attestation_text(verdict: str, server_name: str, trust_score: float, confidence: float) -> str:
    """Build formal attestation text based on verdict."""
    description = VERDICT_DESCRIPTIONS.get(verdict, VERDICT_DESCRIPTIONS['INSUFFICIENT'])
    
    lines = [
        f"# MCP Server Attestation",
        f"## Server: {server_name}",
        f"## Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"### Verdict: {verdict}",
        "",
        f"**Assessment**: {description}",
        "",
        f"### Quantitative Indicators",
        f"- Trust Score: {trust_score:.1f}/100",
        f"- Confidence Level: {confidence:.1%}",
        "",
        f"### Caveats",
        "- This attestation is based on automated analysis only",
        "- This document does not constitute a formal security audit",
        "- Conditions may change; attestations have defined validity periods",
        "- Organizations should perform their own risk assessment",
        ""
    ]
    return "\n".join(lines)


def generate_attestation(server_id: str) -> Optional[Dict[str, Any]]:
    """Generate attestation for a given server_id.
    
    Returns:
        dict with attestation data or None if server not found
    """
    logger.info(f"Generating attestation for server: {server_id}")
    
    # Fetch server data
    server_data = fetch_server_data(server_id)
    if not server_data:
        logger.warning(f"Server {server_id} not found in registry")
        return None
    
    verdict = server_data.get('verdict', 'INSUFFICIENT')
    trust_score = server_data.get('trust_score', 0)
    confidence = server_data.get('confidence', 0)
    server_name = server_data.get('name', server_id)
    
    # Get risk_tier from risk register if available
    risk_tier = fetch_risk_tier(server_id)
    if not risk_tier:
        # Fallback: derive from trust_score
        if trust_score >= 75:
            risk_tier = 'LOW'
        elif trust_score >= 45:
            risk_tier = 'MEDIUM'
        elif trust_score >= 15:
            risk_tier = 'HIGH'
        else:
            risk_tier = 'CRITICAL'
    
    # Determine expiry based on verdict
    expiry_days = VERDICT_EXPIRY.get(verdict, 14)
    valid_until = datetime.now(timezone.utc) + timedelta(days=expiry_days)
    
    # Build attestation text
    attestation_text = build_attestation_text(verdict, server_name, trust_score, confidence)
    
    # Determine scope based on verdict
    if verdict == 'TRUSTED_GENERAL':
        scope = 'General enterprise use permitted'
    elif verdict == 'TRUSTED_RESEARCH':
        scope = 'Research and development environments only'
    elif verdict == 'ENTERPRISE_CONTROLLED':
        scope = 'Controlled use with governance controls required'
    elif verdict == 'CAUTION_LIMITED':
        scope = 'Isolated environments only, enhanced monitoring required'
    elif verdict == 'HIGH_RISK_ISOLATED':
        scope = 'Strictly isolated contexts, no sensitive data'
    elif verdict == 'KNOWN_THREAT':
        scope = 'No deployment authorized'
    else:
        scope = 'Manual review required before deployment'
    
    # Determine confidence level.
    #
    # `mcp_attestations.confidence_level` is FLOAT on the live bus. This module
    # used to write the VARCHAR band into it, which is why EVERY write since
    # 2026-06-09 returned 500 `Conversion Error: Could not convert string 'LOW'
    # to FLOAT` (write_service.log, verbatim) while this daemon logged
    # "Cycle complete. Generated 0 attestations" at INFO -- 117 days, 0 valid
    # attestations, 28 rows in the table all generated in one 13-minute window
    # on 2026-06-09 and all expired on 2026-07-09.
    #
    # The band is DERIVED from the number, so the number is what is stored and
    # the band is what is rendered. Which of the two the column takes is
    # resolved from the LIVE type, not asserted here (R1).
    confidence_band = confidence_band_of(confidence)
    
    # Standard caveats
    caveats = "Automated analysis only; not a formal security audit; verify conditions before deployment"
    
    attestation = {
        'server_id': server_id,
        'attestation_text': attestation_text,
        'scope': scope,
        'confidence_level': confidence_level_for_column(confidence,
                                                        confidence_band),
        'valid_until': valid_until.isoformat(),
        'risk_tier': risk_tier,
        'caveats': caveats,
        'generated_at': datetime.now(timezone.utc).isoformat()
    }
    
    return attestation


def create_attestations_table():
    """Create mcp_attestations table if not exists.

    IF NOT EXISTS, so against the live table this is a no-op -- which is why
    the divergence below went unseen for 117 days. Aligned 2026-10-04 to the
    table write_service actually writes into, read from information_schema:
    `attestation_id VARCHAR` (not `id BIGINT`), `confidence_level FLOAT` (not
    VARCHAR) and a `status VARCHAR` column this file never knew about. The live
    table is NOT altered by this change; only the DDL that would recreate it
    from scratch, so a rebuild stops reintroducing the FLOAT/VARCHAR mismatch.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_attestations (
        attestation_id    VARCHAR,
        server_id         VARCHAR NOT NULL,
        attestation_text  VARCHAR,
        scope             VARCHAR,
        confidence_level  FLOAT,
        valid_until       TIMESTAMPTZ,
        risk_tier         VARCHAR,
        caveats           VARCHAR,
        status            VARCHAR,
        generated_at      TIMESTAMPTZ DEFAULT now(),
        UNIQUE(server_id, generated_at)
    )
    """
    try:
        requests.post(EXECUTE_URL, json={'sql': sql})
        logger.info("mcp_attestations table created or verified")
    except Exception as e:
        logger.error(f"Failed to create attestations table: {e}")


def write_attestation(attestation: Dict[str, Any]) -> dict:
    """Write attestation to mcp_attestations table."""
    return ws_write('mcp_attestations', attestation, wait=True)


def generate_report(all_attestations: list):
    """Generate ATTESTATION_REPORT.md with all attestations."""
    lines = [
        "# ZO-SENTINEL Attestation Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Total Attestations: {len(all_attestations)}",
        "",
        "---",
        ""
    ]
    
    for att in all_attestations:
        lines.append(att.get('attestation_text', ''))
        lines.append("---")
        lines.append("")
    
    report_content = "\n".join(lines)
    
    with open(REPORT_PATH, 'w') as f:
        f.write(report_content)
    
    logger.info(f"Attestation report written to {REPORT_PATH}")


def get_all_servers_needing_attestation() -> list:
    """Get all servers that need attestation (no recent valid attestation)."""
    sql = """
    SELECT server_id, name, verdict, trust_score, confidence
    FROM mcp_server_registry
    WHERE verdict IS NOT NULL
    AND trust_score IS NOT NULL
    AND server_id NOT IN (
        SELECT server_id 
        FROM mcp_attestations 
        WHERE valid_until > now()
    )
    """
    try:
        return ws_query(sql)
    except Exception as e:
        # R6: unknown is not zero. Returning [] here told cycle() that NO
        # server needed attestation, which is indistinguishable from a healthy
        # fleet and is how a read failure presented as a clean cycle. Raise, so
        # run()'s handler records a failed cycle instead of a quiet pass.
        logger.error(f"Failed to query servers needing attestation: {e}")
        raise


def cycle():
    """Main work cycle - generate attestations for servers."""
    logger.info("Starting attestation cycle")
    
    # Ensure table exists
    create_attestations_table()
    
    # Get servers needing attestation
    servers = get_all_servers_needing_attestation()
    logger.info(f"Found {len(servers)} servers needing attestation")
    
    all_attestations = []
    failures = []
    
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        try:
            attestation = generate_attestation(server_id)
            if attestation:
                write_attestation(attestation)
                all_attestations.append(attestation)
                logger.info(f"Generated attestation for {server_id}")
        except Exception as e:
            failures.append((server_id, str(e)))
            logger.error(f"Failed to generate attestation for {server_id}: {e}")
    
    # Generate consolidated report if we have attestations
    if all_attestations:
        generate_report(all_attestations)
    
    logger.info(f"Cycle complete. Generated {len(all_attestations)} attestations")

    if servers and not all_attestations:
        # The condition nobody saw from 2026-06-09 to 2026-10-04: a cycle that
        # attempted work, produced nothing, and said so at INFO. Attempted > 0
        # with 0 written is a service that is DOWN while its process is UP, so
        # it is logged as such -- the one line that would have surfaced the
        # FLOAT/VARCHAR mismatch on day one. Not a gate: no new required check,
        # nothing blocks, and a cycle with nothing to do stays silent.
        first_id, first_err = failures[0] if failures else (
            None, 'every candidate was skipped before any write was attempted')
        logger.error(
            "ATTESTATION CYCLE PRODUCED NOTHING: attempted=%d written=0 "
            "failed=%d first_failure=%s: %s",
            len(servers), len(failures), first_id, first_err)
    return len(all_attestations)


def check_single_instance(*_args, **_kwargs):
    """Single-instance lock, identity-verified. See singleton_lock.py.

    Was: os.kill(pid, 0) -- "does SOME process own this number?" That let a
    recycled PID wedge this daemon shut permanently (2026-09-21, gh#5412).
    """
    _svc = globals().get("SERVICE_NAME") or os.path.splitext(
        os.path.basename(__file__))[0]
    return singleton_lock.check_single_instance(_svc, script=__file__)

def run():
    """Main run loop with heartbeat and cycle management."""
    check_single_instance()
    logger.info(f"{SERVICE_NAME} starting")
    
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    run()