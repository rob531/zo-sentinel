import logging
import os
from datetime import datetime, timezone
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/verdict_explainer.log'
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'

VERDICT_EXPLANATIONS = {
    'TRUSTED_GENERAL': 'This server has been assessed as broadly trusted for general use, with strong evidence across multiple security dimensions.',
    'TRUSTED_RESEARCH': 'This server is trusted based on research-grade analysis, with verified security properties and stable behavior patterns.',
    'ENTERPRISE_CONTROLLED': 'This server operates within enterprise-controlled boundaries with validated governance and compliance measures.',
    'CAUTION_LIMITED': 'This server presents limited caution concerns - some risk factors identified but manageable with appropriate safeguards.',
    'AMBER_UNVERIFIED': 'This server has not been fully verified. Exercise caution and conduct additional assessment before deployment.',
    'HIGH_RISK_ISOLATED': 'This server exhibits high-risk characteristics and should be deployed in isolated environments with strict access controls.',
    'KNOWN_THREAT': 'This server has been identified as a known threat vector based on confirmed malicious indicators or patterns.',
    'UNKNOWN': 'This server has not been assessed. Submit it for evaluation to receive a security verdict.',
    'INSUFFICIENT': 'This server lacks sufficient assessment data to generate a reliable security verdict.'
}

SIGNAL_EXPLANATIONS = {
    'injection_resilience': {
        'high': 'Strong resilience against prompt injection attempts. Tool descriptions and parameters do not exhibit injection-prone patterns.',
        'medium': 'Moderate injection resilience. Some areas may benefit from additional sanitization.',
        'low': 'Potential vulnerability to prompt injection. Tool schemas or descriptions may contain risky patterns.',
        'missing': 'No injection resilience assessment available for this server.'
    },
    'tool_description_safety': {
        'high': 'Tool descriptions are clear, well-documented, and free from suspicious patterns such as injection attempts or credential harvesting.',
        'medium': 'Tool descriptions contain minor concerns requiring manual review before deployment.',
        'low': 'Tool descriptions contain significant security concerns or patterns consistent with credential harvesting.',
        'missing': 'No tool description safety assessment available for this server.'
    },
    'permission_scope': {
        'high': 'Permission requests follow least-privilege principles. Only minimal, necessary permissions are requested.',
        'medium': 'Permission requests are moderate in scope with acceptable risk profiles for intended use cases.',
        'low': 'Permission requests are excessive and pose elevated risk to system security if granted.',
        'missing': 'No permission scope assessment available for this server.'
    },
    'supply_chain': {
        'high': 'Supply chain verification confirms legitimate origins, verified build processes, and trusted publication channels.',
        'medium': 'Supply chain verification shows partial evidence of legitimate origins. Some verification gaps exist.',
        'low': 'Supply chain verification reveals unverified origins, unofficial distribution channels, or suspicious patterns.',
        'missing': 'No supply chain assessment available for this server.'
    },
    'community_signal': {
        'high': 'Community reports indicate positive experiences, verified safe usage patterns, and no reported security incidents.',
        'medium': 'Community signals show mixed reports with some unverified claims. Use with appropriate monitoring.',
        'low': 'Community reports indicate negative experiences, security incidents, or significant trust concerns.',
        'missing': 'No community signal assessment available for this server.'
    },
    'temporal_stability': {
        'high': 'The server demonstrates stable behavior over extended observation periods with no significant behavioral changes.',
        'medium': 'The server shows moderate stability with some behavioral variations over time.',
        'low': 'The server exhibits unstable behavior patterns, recent significant changes, or suspicious activity.',
        'missing': 'No temporal stability assessment available for this server.'
    },
    'domain_trust': {
        'high': 'The server domain has established trust credentials, verified ownership records, and positive reputation.',
        'medium': 'The server domain shows moderate trust indicators with partial verification. Additional verification recommended.',
        'low': 'The server domain lacks established trust, verification records, or exhibits suspicious characteristics.',
        'missing': 'No domain trust assessment available for this server.'
    },
    'context_efficiency': {
        'high': 'Tool schemas are well-designed and efficient, minimizing unnecessary context overhead.',
        'medium': 'Tool schemas are reasonably efficient with some room for optimization.',
        'low': 'Tool schemas are inefficient or overly verbose, potentially causing context management issues.',
        'missing': 'No context efficiency assessment available for this server.'
    }
}

SIGNAL_WEIGHTS = {
    'injection_resilience': 0.20,
    'tool_description_safety': 0.18,
    'permission_scope': 0.15,
    'supply_chain': 0.17,
    'community_signal': 0.12,
    'temporal_stability': 0.10,
    'domain_trust': 0.05,
    'context_efficiency': 0.03
}


def ws_query(sql: str, params: dict = None) -> list[dict[str, Any]]:
    import requests
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql, 'params': params or {}},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get('rows', result.get('results', []))
    except requests.exceptions.RequestException as e:
        logger.error(f'Query failed: {e}')
        return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    import requests
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f'Write failed: {e}')
        return False


def get_server_from_registry(server_id: str) -> dict[str, Any] | None:
    sql = '''
        SELECT server_id, name, url, description, verdict, trust_score, registry_source, scan_count
        FROM mcp_server_registry
        WHERE server_id = ?
    '''
    rows = ws_query(sql, {'p0': server_id})
    if rows:
        return rows[0]
    return None


def get_signal_scores(server_id: str) -> list[dict[str, Any]]:
    sql = '''
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY signal_name
    '''
    return ws_query(sql, {'p0': server_id})


def get_threat_associations(server_id: str) -> list[dict[str, Any]]:
    sql = '''
        SELECT threat_type, severity, evidence, reported_at
        FROM mcp_threat_associations
        WHERE server_id = ?
        ORDER BY severity DESC, reported_at DESC
    '''
    return ws_query(sql, {'p0': server_id})


def get_risk_register(server_id: str) -> dict[str, Any] | None:
    sql = '''
        SELECT risk_tier, risk_rank, threat_count, computed_at
        FROM mcp_risk_register
        WHERE server_id = ?
    '''
    rows = ws_query(sql, {'p0': server_id})
    if rows:
        return rows[0]
    return None


def score_band(score: float) -> str:
    if score >= 0.75:
        return 'high'
    elif score >= 0.40:
        return 'medium'
    else:
        return 'low'


def format_signal_level(signal_name: str, score: float, evidence: str = '') -> dict[str, Any]:
    band = score_band(score)
    signal_key = signal_name.lower().replace('-', '_').replace(' ', '_')
    explanation_base = SIGNAL_EXPLANATIONS.get(signal_key, SIGNAL_EXPLANATIONS.get(signal_name, {
        'high': 'Strong positive signal.',
        'medium': 'Moderate signal.',
        'low': 'Weak or concerning signal.',
        'missing': 'No assessment available.'
    }))
    if isinstance(explanation_base, dict):
        explanation = explanation_base.get(band, explanation_base.get('missing', 'No assessment available.'))
    else:
        explanation = explanation_base

    return {
        'signal': signal_name,
        'score': round(score, 3),
        'band': band,
        'explanation': explanation,
        'evidence': evidence if evidence else None,
        'contribution': round(SIGNAL_WEIGHTS.get(signal_name, 0.0) * score, 4)
    }


def get_verdict_explanation(server_id: str) -> dict[str, Any]:
    server = get_server_from_registry(server_id)
    if not server:
        return {
            'server_id': server_id,
            'found': False,
            'error': 'Server not found in registry'
        }

    verdict = server.get('verdict', 'UNKNOWN')
    trust_score = server.get('trust_score')
    risk_register = get_risk_register(server_id)
    threat_associations = get_threat_associations(server_id)
    signal_scores = get_signal_scores(server_id)

    signals_formatted = []
    for sig in signal_scores:
        signals_formatted.append(format_signal_level(
            sig.get('signal_name', 'unknown'),
            float(sig.get('score', 0)),
            sig.get('evidence', '')
        ))

    verdict_explanation = VERDICT_EXPLANATIONS.get(verdict, VERDICT_EXPLANATIONS.get('UNKNOWN', 'No verdict information available.'))

    threats = []
    for t in threat_associations:
        threats.append({
            'type': t.get('threat_type'),
            'severity': t.get('severity'),
            'evidence': t.get('evidence'),
            'reported_at': t.get('reported_at')
        })

    result = {
        'server_id': server_id,
        'found': True,
        'server_name': server.get('name'),
        'verdict': verdict,
        'trust_score': trust_score,
        'verdict_explanation': verdict_explanation,
        'risk_tier': risk_register.get('risk_tier') if risk_register else None,
        'risk_rank': risk_register.get('risk_rank') if risk_register else None,
        'threat_count': len(threats),
        'threats': threats,
        'signals': signals_formatted,
        'registry_source': server.get('registry_source'),
        'scan_count': server.get('scan_count')
    }

    return result


def format_signal_breakdown(server_id: str) -> str:
    data = get_verdict_explanation(server_id)
    if not data.get('found'):
        return f"Server {server_id} not found in registry."

    lines = []
    lines.append(f"=== Verdict Explanation for: {data['server_name']} ({server_id}) ===")
    lines.append(f"Verdict: {data['verdict']}")
    lines.append(f"Trust Score: {data['trust_score']:.2f}" if data['trust_score'] else "Trust Score: N/A")
    lines.append(f"Risk Tier: {data['risk_tier'] or 'Not assessed'}")
    lines.append("")
    lines.append("Verdict Explanation:")
    lines.append(f"  {data['verdict_explanation']}")
    lines.append("")

    if data['threats']:
        lines.append(f"Threats ({len(data['threats'])}):")
        for t in data['threats']:
            lines.append(f"  [{t['severity'].upper()}] {t['type']}")
            if t['evidence']:
                lines.append(f"    Evidence: {t['evidence'][:200]}...")
        lines.append("")

    if data['signals']:
        lines.append(f"Signal Breakdown ({len(data['signals'])} signals):")
        for sig in data['signals']:
            contrib_pct = sig['contribution'] * 100
            lines.append(f"  {sig['signal']}: {sig['score']:.3f} ({sig['band']}) - contrib: {contrib_pct:.1f}%")
            lines.append(f"    {sig['explanation']}")
        lines.append("")

    return "\n".join(lines)


def get_all_verdicts_summary() -> list[dict[str, Any]]:
    sql = '''
        SELECT verdict, COUNT(*) as count
        FROM mcp_server_registry
        WHERE verdict IS NOT NULL
        GROUP BY verdict
        ORDER BY count DESC
    '''
    return ws_query(sql)


def record_explanation_request(server_id: str, explanation: dict[str, Any]) -> bool:
    import hashlib
    from datetime import datetime, timezone

    request_id = hashlib.sha256(
        f"{server_id}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:32]

    row = {
        'request_id': request_id,
        'server_id': server_id,
        'verdict': explanation.get('verdict'),
        'trust_score': explanation.get('trust_score'),
        'signal_count': len(explanation.get('signals', [])),
        'threat_count': len(explanation.get('threats', [])),
        'requested_at': datetime.now(timezone.utc).isoformat()
    }

    return ws_write('verdict_explanation_requests', [row])


def check_explanation_cache(server_id: str, max_age_hours: int = 24) -> dict[str, Any] | None:
    sql = '''
        SELECT request_id, verdict, trust_score, signal_count, requested_at
        FROM verdict_explanation_requests
        WHERE server_id = ?
        AND requested_at > NOW() - INTERVAL '%d hours'
        ORDER BY requested_at DESC
        LIMIT 1
    ''' % max_age_hours
    rows = ws_query(sql, {'p0': server_id})
    if rows:
        return rows[0]
    return None


def ensure_explanation_requests_table() -> None:
    import requests as req
    create_sql = '''
        CREATE TABLE IF NOT EXISTS verdict_explanation_requests (
            request_id VARCHAR(64) PRIMARY KEY,
            server_id VARCHAR(128),
            verdict VARCHAR(64),
            trust_score DOUBLE,
            signal_count INTEGER,
            threat_count INTEGER,
            requested_at TIMESTAMPTZ
        )
    '''
    try:
        resp = req.post(
            WRITE_SERVICE_URL,
            json={'sql': create_sql},
            timeout=30
        )
        resp.raise_for_status()
    except req.exceptions.RequestException as e:
        logger.warning(f'Could not create explanation requests table: {e}')


def run():
    import time
    logger.info('Verdict explainer service started')
    ensure_explanation_requests_table()

    while True:
        try:
            summary = get_all_verdicts_summary()
            logger.info(f'Verdict distribution: {summary}')
        except Exception as e:
            logger.error(f'Error in cycle: {e}')

        time.sleep(3600)


if __name__ == '__main__':
    run()