#!/usr/bin/env python3
"""
similarity_scorer.py -- ZO-SENTINEL similarity scoring utility.
Uses character n-gram overlap to find suspicious similarities.
No ML dependencies required.
"""
import requests
import logging
from typing import List, Dict, Any, Tuple, Optional

log = logging.getLogger(__name__)

# Service endpoints
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"

# Known threat patterns for squatting detection
SQUATTING_KEYWORDS = [
    "fake", "clone", "malicious", "stealer", "spy", "tracker",
    "trojan", "backdoor", "keylogger", "malware"
]


def _get_ngrams(text: str, n: int) -> set:
    """Generate character n-grams from text."""
    text_lower = text.lower().strip()
    if len(text_lower) < n:
        return {text_lower} if text_lower else set()
    return {text_lower[i:i+n] for i in range(len(text_lower) - n + 1)}


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def name_similarity(a: str, b: str) -> float:
    """Calculate similarity between two names using character n-gram overlap.
    
    Args:
        a: First server name
        b: Second server name
    
    Returns:
        Float between 0.0 and 1.0 indicating similarity
    """
    if not a or not b:
        return 0.0
    if a.lower() == b.lower():
        return 1.0
    
    ngrams_a = _get_ngrams(a, 3)
    ngrams_b = _get_ngrams(b, 3)
    return _jaccard_similarity(ngrams_a, ngrams_b)


def description_similarity(a: str, b: str) -> float:
    """Calculate similarity between two description strings.
    
    Args:
        a: First description
        b: Second description
    
    Returns:
        Float between 0.0 and 1.0 indicating similarity
    """
    if not a or not b:
        return 0.0
    
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    
    if not words_a or not words_b:
        return 0.0
    
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    
    return intersection / union if union > 0 else 0.0


def _ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Execute a query against write_service."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except requests.exceptions.RequestException as e:
        log.error(f"Query failed: {e}")
        return []
    except Exception as e:
        log.error(f"Query parse error: {e}")
        return []


def _ws_write(rows: List[Dict[str, Any]], table: str) -> bool:
    """Write data via write_service POST /write with 'rows'."""
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            "table": table,
            "rows": rows
        }, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Write failed: {e}")
        return False


def _load_known_threats() -> Dict[str, Any]:
    """Load known threats from known_threats module if available."""
    try:
        from known_threats import (
            KNOWN_MALICIOUS_PACKAGES,
            KNOWN_MALICIOUS_DOMAINS,
            HIGH_RISK_PATTERNS,
            SUSPICIOUS_PERMISSIONS
        )
        return {
            "packages": KNOWN_MALICIOUS_PACKAGES,
            "domains": KNOWN_MALICIOUS_DOMAINS,
            "patterns": HIGH_RISK_PATTERNS,
            "permissions": SUSPICIOUS_PERMISSIONS
        }
    except ImportError:
        log.warning("known_threats module not available, using defaults")
        return {
            "packages": [
                "fake-postmark-mcp",
                "mcp-server-postmark-fake",
                "@mcp/server-postmark-clone",
                "mcp-whatsapp-stealer",
                "mcp-server-all",
                "@modelcontextprotocol/server-all",
            ],
            "domains": [
                "evil-mcp.io",
                "mcp-tools.ru",
                "mcpserver.xyz",
            ],
            "patterns": [
                "<IMPORTANT>",
                "ignore previous instructions",
                "send.*to.*https?://",
                "exfiltrat",
                "~/.ssh",
                "steal",
                "secretly",
            ],
            "permissions": [
                "filesystem", "execute", "shell", "subprocess",
                "ssh", "credentials", "keychain", "env_vars"
            ]
        }


def find_similar_to_known_threats(server_id: str) -> List[Dict[str, Any]]:
    """Check if a server is similar to known malicious packages or domains.
    
    Args:
        server_id: The server to check
    
    Returns:
        List of threat matches with score and severity
    """
    threats = _load_known_threats()
    
    query = """
        SELECT server_id, name, description, url, registry_source, trust_score
        FROM mcp_server_registry
        WHERE server_id = ?
    """
    results = _ws_query(query, [server_id])
    
    if not results:
        return []
    
    server = results[0]
    server_name = server.get('name', '') or ''
    server_desc = server.get('description', '') or ''
    server_url = server.get('url', '') or ''
    
    matches = []
    
    for threat_pkg in threats["packages"]:
        name_sim = name_similarity(server_name, threat_pkg)
        if name_sim >= 0.6:
            matches.append({
                'server_id': server_id,
                'match_type': 'package_similarity',
                'threat_value': threat_pkg,
                'similarity_score': round(name_sim, 3),
                'severity': 'critical' if name_sim >= 0.85 else 'high'
            })
    
    for threat_domain in threats["domains"]:
        if threat_domain in server_url.lower():
            matches.append({
                'server_id': server_id,
                'match_type': 'malicious_domain',
                'threat_value': threat_domain,
                'similarity_score': 1.0,
                'severity': 'critical'
            })
    
    combined_text = (server_name + " " + server_desc).lower()
    for pattern in threats["patterns"]:
        pattern_lower = pattern.lower()
        if pattern_lower in combined_text:
            matches.append({
                'server_id': server_id,
                'match_type': 'high_risk_pattern',
                'threat_value': pattern,
                'similarity_score': 1.0,
                'severity': 'high'
            })
    
    return matches


def find_namespace_squatting(threshold: float = 0.85) -> List[Tuple[str, str, float]]:
    """Find potential namespace squatting between server pairs.
    
    Compares server names using n-gram similarity to detect typosquatting,
    brand impersonation, and other namespace attacks.
    
    Args:
        threshold: Minimum similarity score (0.0 to 1.0)
    
    Returns:
        List of (server_a, server_b, similarity) tuples sorted by score
    """
    query = """
        SELECT server_id, name, description, url
        FROM mcp_server_registry
        WHERE trust_score < 0.7
        LIMIT 500
    """
    servers = _ws_query(query)
    
    if len(servers) < 2:
        return []
    
    squatting_pairs = []
    checked: set = set()
    
    for i, server_a in enumerate(servers):
        for server_b in servers[i+1:]:
            id_a = server_a['server_id']
            id_b = server_b['server_id']
            pair_key = tuple(sorted([id_a, id_b]))
            if pair_key in checked:
                continue
            checked.add(pair_key)
            
            sim = name_similarity(
                server_a.get('name', '') or '',
                server_b.get('name', '') or ''
            )
            
            if sim >= threshold:
                squatting_pairs.append((
                    id_a,
                    id_b,
                    round(sim, 3)
                ))
    
    squatting_pairs.sort(key=lambda x: x[2], reverse=True)
    
    return squatting_pairs[:100]


def compute_name_hash(name: str) -> str:
    """Compute a normalized hash for name comparison."""
    import hashlib
    normalized = name.lower().strip().replace('_', '-').replace('@', '')
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def find_typosquat_variants(official_names: List[str], candidate_names: List[str]) -> List[Dict[str, Any]]:
    """Find potential typosquat variants between official and candidate names.
    
    Args:
        official_names: List of known legitimate package names
        candidate_names: List of potential malicious packages to check
    
    Returns:
        List of detected typosquat matches with similarity scores
    """
    results = []
    
    for candidate in candidate_names:
        for official in official_names:
            sim = name_similarity(candidate, official)
            
            if sim >= 0.7 and sim < 1.0:
                edits = _levenshtein_distance(candidate, official)
                results.append({
                    'candidate': candidate,
                    'official': official,
                    'similarity': round(sim, 3),
                    'edit_distance': edits,
                    'likely_type': _classify_typosquat_variant(candidate, official)
                })
    
    results.sort(key=lambda x: x['similarity'], reverse=True)
    return results


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def _classify_typosquat_variant(candidate: str, official: str) -> str:
    """Classify the type of typosquat variant."""
    if candidate.startswith('@') and not official.startswith('@'):
        return 'namespace_addition'
    
    if candidate.replace('-', '').replace('_', '') == official.replace('-', '').replace('_', ''):
        return 'separator_swap'
    
    if candidate in official or official in candidate:
        return 'substring_match'
    
    cdiff = _levenshtein_distance(candidate, official)
    max_len = max(len(candidate), len(official))
    
    if cdiff == 1:
        return 'single_char_edit'
    elif cdiff == 2 and max_len > 8:
        return 'double_char_edit'
    else:
        return 'high_similarity'


def batch_similarity_check(
    target_server_id: str,
    candidate_server_ids: Optional[List[str]] = None,
    threshold: float = 0.5
) -> List[Dict[str, Any]]:
    """Check similarity between target server and candidates.
    
    Args:
        target_server_id: Server to compare from
        candidate_server_ids: Specific candidates to check (None = all)
        threshold: Minimum combined score to include in results
    
    Returns:
        List of similarity results sorted by combined score
    """
    if candidate_server_ids:
        placeholders = ','.join(['?' for _ in candidate_server_ids])
        query = f"""
            SELECT server_id, name, description, trust_score, verdict
            FROM mcp_server_registry
            WHERE server_id IN ({placeholders})
        """
        candidates = _ws_query(query, candidate_server_ids)
    else:
        query = """
            SELECT server_id, name, description, trust_score, verdict
            FROM mcp_server_registry
            WHERE trust_score < 0.8
            LIMIT 200
        """
        candidates = _ws_query(query)
    
    query = """
        SELECT server_id, name, description
        FROM mcp_server_registry
        WHERE server_id = ?
    """
    targets = _ws_query(query, [target_server_id])
    
    if not targets:
        return []
    
    target = targets[0]
    target_name = target.get('name', '') or ''
    target_desc = target.get('description', '') or ''
    
    results = []
    for candidate in candidates:
        if candidate['server_id'] == target_server_id:
            continue
        
        cand_name = candidate.get('name', '') or ''
        cand_desc = candidate.get('description', '') or ''
        
        name_sim = name_similarity(target_name, cand_name)
        desc_sim = description_similarity(target_desc, cand_desc)
        
        combined = (name_sim * 0.7) + (desc_sim * 0.3)
        
        if combined >= threshold:
            results.append({
                'target_server_id': target_server_id,
                'candidate_server_id': candidate['server_id'],
                'name_similarity': round(name_sim, 3),
                'description_similarity': round(desc_sim, 3),
                'combined_score': round(combined, 3),
                'candidate_name': cand_name
            })
    
    results.sort(key=lambda x: x['combined_score'], reverse=True)
    return results


def get_similarity_metrics(server_id: str) -> Dict[str, Any]:
    """Get comprehensive similarity metrics for a server.
    
    Args:
        server_id: Server to analyze
    
    Returns:
        Dictionary with similarity statistics and threat matches
    """
    threats = find_similar_to_known_threats(server_id)
    squatting = find_namespace_squatting(threshold=0.85)
    
    direct_squat = [s for s in squatting if s[0] == server_id or s[1] == server_id]
    
    query = """
        SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE server_id = ?
    """
    signal_count = _ws_query(query, [server_id])
    
    return {
        'server_id': server_id,
        'known_threat_matches': len(threats),
        'has_critical_threat': any(t['severity'] == 'critical' for t in threats),
        'has_high_risk_pattern': any(t['match_type'] == 'high_risk_pattern' for t in threats),
        'namespace_squat_count': len(direct_squat),
        'signal_count': signal_count[0]['cnt'] if signal_count else 0,
        'threat_details': threats[:5]
    }


def rank_by_similarity_risk(threshold: float = 0.75) -> List[Dict[str, Any]]:
    """Rank all servers by their similarity to threats.
    
    Args:
        threshold: Minimum similarity to flag
    
    Returns:
        List of servers with similarity risk scores
    """
    threats = _load_known_threats()
    
    query = """
        SELECT server_id, name, description, url, trust_score, verdict
        FROM mcp_server_registry
        WHERE trust_score < 0.8
        LIMIT 500
    """
    servers = _ws_query(query)
    
    results = []
    for server in servers:
        sid = server['server_id']
        name = server.get('name', '') or ''
        url = server.get('url', '') or ''
        
        max_sim = 0.0
        matched_threat = None
        
        for pkg in threats["packages"]:
            sim = name_similarity(name, pkg)
            if sim > max_sim:
                max_sim = sim
                matched_threat = pkg
        
        for domain in threats["domains"]:
            if domain in url.lower():
                max_sim = 1.0
                matched_threat = domain
                break
        
        if max_sim >= threshold:
            results.append({
                'server_id': sid,
                'name': name,
                'max_similarity': round(max_sim, 3),
                'matched_threat': matched_threat,
                'trust_score': server.get('trust_score'),
                'verdict': server.get('verdict')
            })
    
    results.sort(key=lambda x: x['max_similarity'], reverse=True)
    return results


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--test':
            print("Testing similarity functions...")
            
            assert name_similarity("fake-postmark-mcp", "fake-postmark-server") > 0.7
            assert name_similarity("@modelcontextprotocol/server-all", "mcp-server-all") > 0.6
            assert name_similarity("hello", "world") < 0.5
            assert description_similarity("A test description", "A test description") == 1.0
            assert description_similarity("foo bar", "baz qux") == 0.0
            
            print("All tests passed!")
            
        elif sys.argv[1] == '--squatting':
            print("Searching for namespace squatting...")
            squatting = find_namespace_squatting(threshold=0.85)
            print(f"Found {len(squatting)} squatting pairs")
            for pair in squatting[:10]:
                print(f"  {pair}")
                
        elif sys.argv[1] == '--rank':
            print("Ranking servers by similarity risk...")
            ranked = rank_by_similarity_risk(threshold=0.75)
            print(f"Found {len(ranked)} at-risk servers")
            for r in ranked[:10]:
                print(f"  {r['name']}: {r['max_similarity']} ({r['matched_threat']})")
        else:
            print("Usage:")
            print("  python similarity_scorer.py --test      # Run tests")
            print("  python similarity_scorer.py --squatting  # Find squatting")
            print("  python similarity_scorer.py --rank       # Rank by risk")
    else:
        print("Usage:")
        print("  python similarity_scorer.py --test      # Run tests")
        print("  python similarity_scorer.py --squatting  # Find squatting")
        print("  python similarity_scorer.py --rank       # Rank by risk")