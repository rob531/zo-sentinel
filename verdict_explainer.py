import logging
from typing import Dict, Any, Optional, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE_URL = "http://127.0.0.1:8773"
QUERY_URL = "http://127.0.0.1:8773/query"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

VERDICT_EXPLANATIONS = {
    'TRUSTED_GENERAL': 'This server has been assessed as broadly trusted for general use, with strong evidence across multiple security dimensions.',
    'TRUSTED_RESEARCH': 'This server is trusted based on research-grade analysis, with verified security properties and stable behavior patterns.',
    'ENTERPRISE_CONTROLLED': 'This server operates within enterprise-controlled boundaries with validated governance and compliance measures.',
    'CAUTION_LIMITED': 'This server presents limited caution concerns - some risk factors identified but manageable with appropriate safeguards.',
    'HIGH_RISK_ISOLATED': 'This server exhibits high-risk characteristics and should be deployed in isolated environments with strict access controls.',
    'KNOWN_THREAT': 'This server has been identified as a known threat vector based on confirmed malicious indicators or patterns.',
    'INSUFFICIENT': 'This server lacks sufficient assessment data to generate a reliable security verdict.'
}

SIGNAL_EVIDENCE_TEMPLATES = {
    'domain_trust': {
        'high': 'The server domain has established trust credentials and verified ownership records.',
        'medium': 'The server domain shows moderate trust indicators with partial verification.',
        'low': 'The server domain lacks established trust or verification records.'
    },
    'tool_description_safety': {
        'high': 'Tool descriptions are free from suspicious patterns such as injection attempts, credential harvesting, or obfuscation.',
        'medium': 'Tool descriptions contain minor security concerns requiring manual review.',
        'low': 'Tool descriptions contain significant security concerns or malicious patterns.'
    },
    'permission_scope': {
        'high': 'Permission requests are minimal and follow least-privilege principles.',
        'medium': 'Permission requests are moderate in scope with acceptable risk profiles.',
        'low': 'Permission requests are excessive and pose elevated risk to system security.'
    },
    'supply_chain': {
        'high': 'Supply chain verification confirms legitimate origins and verified build processes.',
        'medium': 'Supply chain verification shows partial evidence of legitimate origins.',
        'low': 'Supply chain verification reveals unverified or suspicious origins.'
    },
    'community_signal': {
        'high': 'Community reports indicate positive experiences and verified safe usage patterns.',
        'medium': 'Community signals show mixed reports with some unverified claims.',
        'low': 'Community reports indicate negative experiences or confirmed security incidents.'
    },
    'temporal_stability': {
        'high': 'The server demonstrates stable behavior over extended observation periods.',
        'medium': 'The server shows moderate stability with some behavioral variations.',
        'low': 'The server exhibits unstable behavior patterns or recent significant changes.'
    }
}

def ws_query(sql: str, params: dict = None) -> List[Dict[str, Any]]:
    import requests
    try:
        response = requests.post(QUERY_URL, json={'sql': sql, 'params': params or {}}, timeout=30)
        response.raise_for_status()
        return response.json().get('results', [])
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    import requests
    try:
        response = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows}, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return {}

def fetch_server_verdict_data(server_id: str) -> Optional[Dict[str, Any]]:
    query = """
    SELECT 
        ms.server_id,
        ms.name,
        ms.trust_score,
        ms.verdict,
        ms.confidence,
        ms.assessment_count,
        ms.last_evaluated,
        ms.signals
    FROM mcp_servers ms
    WHERE ms.server_id = ?
    """
    results = ws_query(query, {'server_id': server_id})
    if not results:
        logger.warning(f"No server found with id: {server_id}")
        return None
    return results[0]

def fetch_latest_signals(server_id: str) -> Optional[Dict[str, Any]]:
    query = """
    SELECT signals_data
    FROM scoring_history
    WHERE server_id = ?
    ORDER BY scored_at DESC
    LIMIT 1
    """
    results = ws_query(query, {'server_id': server_id})
    if not results:
        return None
    row = results[0]
    signals_data = row.get('signals_data', '{}')
    if isinstance(signals_data, str):
        import json
        try:
            return json.loads(signals_data)
        except:
            return {}
    return signals_data

def get_signal_level(score: float) -> str:
    if score >= 0.7:
        return 'high'
    elif score >= 0.4:
        return 'medium'
    else:
        return 'low'

def get_signal_evidence(signal_name: str, score: float) -> str:
    level = get_signal_level(score)
    template = SIGNAL_EVIDENCE_TEMPLATES.get(signal_name, {}).get(level, 'No evidence available for this signal.')
    return template

def rank_signals_by_impact(signals: Dict[str, float]) -> List[tuple]:
    signal_weights = {
        'domain_trust': 0.20,
        'tool_description_safety': 0.20,
        'permission_scope': 0.15,
        'supply_chain': 0.15,
        'community_signal': 0.15,
        'temporal_stability': 0.15
    }
    weighted_scores = []
    for signal_name, signal_value in signals.items():
        weight = signal_weights.get(signal_name, 0.10)
        weighted_scores.append((signal_name, signal_value, weight, signal_value * weight))
    weighted_scores.sort(key=lambda x: x[3], reverse=True)
    return weighted_scores

def generate_verdict_explanation(
    name: str,
    verdict: str,
    trust_score: float,
    signals: Dict[str, float],
    confidence: float,
    assessment_count: int
) -> str:
    trust_score_display = int(trust_score * 100)
    confidence_display = int(confidence * 100) if confidence else 50
    signal_count = len(signals)
    assessment_text = f"{assessment_count} assessment{'s' if assessment_count != 1 else ''}" if assessment_count else "initial assessment"
    
    ranked_signals = rank_signals_by_impact(signals)
    
    if ranked_signals:
        top_signal_name, top_signal_score, top_signal_weight, top_weighted = ranked_signals[0]
        top_score_display = int(top_signal_score * 100)
        top_evidence = get_signal_evidence(top_signal_name, top_signal_score)
        top_signal_readable = top_signal_name.replace('_', ' ')
    else:
        top_signal_name = 'overall assessment'
        top_signal_score = trust_score
        top_score_display = trust_score_display
        top_evidence = VERDICT_EXPLANATIONS.get(verdict, 'Assessment based on available data.')
        top_signal_readable = 'overall assessment'
    
    offset_reinforce_text = ''
    if len(ranked_signals) >= 2:
        second_signal_name, second_signal_score, _, second_weighted = ranked_signals[1]
        second_score_display = int(second_signal_score * 100)
        second_evidence = get_signal_evidence(second_signal_name, second_signal_score)
        second_signal_readable = second_signal_name.replace('_', ' ')
        
        if second_weighted > 0:
            if second_signal_score > top_signal_score * 0.9:
                offset_reinforce_text = f" This was reinforced by {second_signal_readable} (score: {second_score_display}/100): {second_evidence}"
            else:
                offset_reinforce_text = f" This was partially offset by {second_signal_readable} (score: {second_score_display}/100): {second_evidence}"
        else:
            offset_reinforce_text = f" However, {second_signal_readable} (score: {second_score_display}/100) presented challenges: {second_evidence}"
    
    additional_context = ''
    if len(ranked_signals) >= 3:
        negative_signals = [s for s in ranked_signals[1:] if s[1] < 0.4]
        if negative_signals:
            neg_names = ', '.join([s[0].replace('_', ' ') for s in negative_signals[:2]])
            additional_context = f" Additional concerns were noted regarding {neg_names}."
    
    verdict_explanation = VERDICT_EXPLANATIONS.get(verdict, 'Assessment based on available security indicators.')
    
    explanation = (
        f"{name} received verdict {verdict} (trust score: {trust_score_display}/100). "
        f"{verdict_explanation} "
        f"The strongest contributing factor was {top_signal_readable} (score: {top_score_display}/100): {top_evidence}"
        f"{offset_reinforce_text}"
        f"{additional_context} "
        f"The assessment carries {confidence_display}% confidence based on {signal_count} scoring dimensions "
        f"across {assessment_text}."
    )
    
    return explanation

def explain_verdict(server_id: str) -> str:
    server_data = fetch_server_verdict_data(server_id)
    if not server_data:
        return f"Unable to generate verdict explanation: Server with ID '{server_id}' not found in registry."
    
    name = server_data.get('name', 'Unknown Server')
    verdict = server_data.get('verdict', 'INSUFFICIENT')
    trust_score = server_data.get('trust_score', 0.0)
    confidence = server_data.get('confidence', 0.5)
    assessment_count = server_data.get('assessment_count', 0)
    
    signals = fetch_latest_signals(server_id)
    if not signals:
        signals_str = server_data.get('signals')
        if signals_str:
            if isinstance(signals_str, str):
                import json
                try:
                    signals = json.loads(signals_str)
                except:
                    signals = {}
            else:
                signals = signals_str
        else:
            signals = {}
    
    if not signals:
        signals = {
            'domain_trust': trust_score,
            'tool_description_safety': trust_score,
            'permission_scope': trust_score,
            'supply_chain': trust_score,
            'community_signal': trust_score,
            'temporal_stability': trust_score
        }
    
    explanation = generate_verdict_explanation(
        name=name,
        verdict=verdict,
        trust_score=trust_score,
        signals=signals,
        confidence=confidence,
        assessment_count=assessment_count
    )
    
    return explanation

def explain_verdict_batch(server_ids: List[str]) -> Dict[str, str]:
    explanations = {}
    for server_id in server_ids:
        try:
            explanations[server_id] = explain_verdict(server_id)
        except Exception as e:
            logger.error(f"Failed to explain verdict for {server_id}: {e}")
            explanations[server_id] = f"Error generating explanation: {str(e)}"
    return explanations

def get_verdict_summary(server_id: str) -> Optional[Dict[str, Any]]:
    server_data = fetch_server_verdict_data(server_id)
    if not server_data:
        return None
    
    signals = fetch_latest_signals(server_id)
    if not signals:
        signals_str = server_data.get('signals')
        if signals_str:
            if isinstance(signals_str, str):
                import json
                try:
                    signals = json.loads(signals_str)
                except:
                    signals = {}
            else:
                signals = signals_str
        else:
            signals = {}
    
    ranked_signals = rank_signals_by_impact(signals) if signals else []
    
    return {
        'server_id': server_id,
        'name': server_data.get('name'),
        'verdict': server_data.get('verdict'),
        'trust_score': server_data.get('trust_score'),
        'confidence': server_data.get('confidence'),
        'top_signal': ranked_signals[0][0] if ranked_signals else None,
        'top_signal_score': ranked_signals[0][1] if ranked_signals else None,
        'second_signal': ranked_signals[1][0] if len(ranked_signals) > 1 else None,
        'second_signal_score': ranked_signals[1][1] if len(ranked_signals) > 1 else None,
        'all_signals': signals,
        'last_evaluated': server_data.get('last_evaluated')
    }

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python verdict_explainer.py <server_id>")
        print("       python verdict_explainer.py --batch <server_id1> <server_id2> ...")
        print("       python verdict_explainer.py --list")
        sys.exit(1)
    
    if sys.argv[1] == '--list':
        query = "SELECT server_id, name, verdict, trust_score FROM mcp_servers ORDER BY name"
        results = ws_query(query)
        if not results:
            print("No servers found in registry.")
            return
        print("\n=== Servers in Registry ===")
        for row in results:
            print(f"  {row.get('name', 'Unknown')} | {row.get('verdict', 'N/A')} | Score: {int(row.get('trust_score', 0) * 100)}")
        return
    
    if sys.argv[1] == '--batch' and len(sys.argv) > 2:
        server_ids = sys.argv[2:]
        explanations = explain_verdict_batch(server_ids)
        for server_id, explanation in explanations.items():
            print(f"\n=== Verdict Explanation for {server_id} ===")
            print(explanation)
        return
    
    server_id = sys.argv[1]
    explanation = explain_verdict(server_id)
    print(explanation)

if __name__ == "__main__":
    main()