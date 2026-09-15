#!/usr/bin/env python3
"""
mcp_fingerprinter.py -- ZO-SENTINEL MCP server fingerprinting.
Generates stable behavioral fingerprints for MCP servers to detect
impersonation and similarity-based attacks.

2026-04-27 patch: ws_query was POSTing to /execute and reading data['results'].
WriteService routes SELECTs to /query and returns data['rows']. This was the
latent reason mcp_fingerprints had 0 rows for the entire history of the table
-- every query returned [] regardless of what was in the DB. Surgical fix to
the helper only; no other behavior changed.
"""
import hashlib
import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
FINGERPRINTS_TABLE = "mcp_fingerprints"

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need",
    "this", "that", "these", "those", "it", "its", "they", "them",
    "their", "what", "which", "who", "whom", "where", "when", "why",
    "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "also", "now", "here", "there",
    "using", "via", "through", "based", "allows", "provides", "enables",
    "service", "server", "client", "api", "http", "https", "url", "data",
    "file", "input", "output", "result", "value", "name", "id", "type"
}

SIGNIFICANT_POS_PATTERNS = [
    r'\b[A-Z][a-z]+\w*\b',
    r'\b\w+(?:ify|tion|ment|ness|able|ible|ous|ive|er|or)\b',
    r'\b\w{4,}\b'
]

def ws_query(sql: str, params: List[Any] = None) -> List[Any]:
    """Execute SELECT via WriteService /query endpoint.

    2026-04-27: was POSTing to /execute and reading data['results'].
    /execute is for DDL/non-SELECT; /query is for SELECT and returns 'rows'.
    Result rows arrive as DICTS keyed by column name (e.g. {'server_id': '...',
    'name': '...'}), so callers should index by column name, not position.
    For backward compatibility this helper returns rows as positional tuples
    that match the original column order in the SELECT clause.
    """
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            return []
        # Convert dict-rows to positional tuples in stable column order.
        # If rows are already lists/tuples (older WS versions), pass through.
        if isinstance(rows[0], (list, tuple)):
            return rows
        if isinstance(rows[0], dict):
            cols = list(rows[0].keys())
            return [tuple(r.get(c) for c in cols) for r in rows]
        return rows
    except Exception as e:
        log.error(f"Query error: {e}")
        return []

def ws_execute(sql: str, params: List[Any] = None) -> bool:
    """Execute DDL / non-SELECT via WriteService /execute endpoint."""
    try:
        payload = {"sql": sql, "wait": True}
        if params:
            payload["params"] = params
        resp = requests.post(EXECUTE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute error: {e}")
        return False

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows via write_service POST /write."""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write error: {e}")
        return False

#: SHA-256 of the empty string. Not a fingerprint -- the ABSENCE of one, wearing
#: a fingerprint's clothes.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def hash_or_absent(content: str) -> Optional[str]:
    """Hash real content; return None for nothing.

    THIS IS THE WHOLE FIX, AND IT IS ONE LINE OF LOGIC.

    `compute_sha256_hash(','.join([]))` is SHA-256("") -- a perfectly
    well-formed 64-hex-character value that is indistinguishable, to every
    consumer, from a genuine fingerprint. Because `mcp_tool_hashes` is empty,
    `get_server_tools` returns [] for every server, and so ALL 3,316 rows of
    `mcp_fingerprints` carry EMPTY_SHA256 in both `tool_name_hash` and
    `permission_scope_hash`. Measured 2026-08-28.

    What that cost, traced end to end:
      - the `tool_count` signal reads this table and scores 91.95 +/- 1.36 for
        every server -- a constant that looks like a measurement
      - `capability_breadth` and `auth_strength` therefore have no real evidence
      - and the v3 scorer, having no way to say "insufficient evidence", emitted
        a confident label anyway, for every server, on every axis

    #4123 corrected WHICH table this reads. It did not stop an empty result
    producing a valid-looking hash, so the defect survived the repair. A hash of
    nothing must be None -- then a consumer can tell, and this codebase's own
    rule applies: "I could not evaluate this" must be distinguishable from
    "this is fine".
    """
    if not content:
        return None
    h = compute_sha256_hash(content)
    return None if h == EMPTY_SHA256 else h


def is_absent_hash(value: Optional[str]) -> bool:
    """True when a stored hash encodes absence rather than content.

    Non-destructive guard for the 3,316 rows already written with EMPTY_SHA256:
    consumers stop reading them as data without anything being deleted.
    """
    return not value or value == EMPTY_SHA256


def compute_sha256_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def compute_jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0

def extract_significant_tokens(text: str, top_n: int = 20) -> Set[str]:
    if not text:
        return set()
    text_lower = text.lower()
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text_lower)
    filtered = [w for w in words if w not in STOPWORDS and len(w) > 2]
    counter = Counter(filtered)
    top_tokens = {word for word, _ in counter.most_common(top_n)}
    return top_tokens

def extract_domain_fingerprint(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        domain = re.sub(r'^www\.', '', domain)
        parts = domain.split('.')
        if len(parts) >= 2:
            base_domain = '.'.join(parts[-2:])
        else:
            base_domain = domain
        return compute_sha256_hash(base_domain.lower())
    except Exception:
        return None

def get_server_tools(server_id: str) -> List[Dict[str, Any]]:
    """Fetch a server's tool definitions from the bus, one dict per tool.

    THERE IS NO `mcp_tool_definitions` TABLE, and there never was.
        This function used to select five per-tool columns from that name. It
        exists on no plane -- not on the bus, not as a __tablename__, in no
        migration -- so ws_query raised on every call and the docstring said so
        out loud: "Tolerant if mcp_tool_definitions doesn't exist yet". It was
        not going to exist later. It was invented at emission time. Refs #4080.

    The bus does hold this data, on `mcp_tool_hashes`, one row per SERVER with
    the whole tool list in `tools_raw`. So the row-per-tool shape this function
    must return is produced HERE, by parsing that payload, rather than asked of
    a table that does not have it.

    `tools_raw` carries the MCP tool-definition shape, so `inputSchema` is
    accepted alongside `input_schema`. NOTE: mcp_tool_hashes is currently empty
    on the bus (0 rows), so this parse is correct-but-unexercised -- which is
    exactly what the old code was, minus the fact that its table could never
    hold anything at all.
    """
    query = """
    SELECT server_id, tools_raw
    FROM mcp_tool_hashes
    WHERE server_id = ?
    """
    results = ws_query(query, [server_id])
    tools: List[Dict[str, Any]] = []
    for row in results:
        try:
            raw = row['tools_raw'] if isinstance(row, dict) else row[1]
            sid = row['server_id'] if isinstance(row, dict) else row[0]
        except Exception:
            continue
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if isinstance(raw, dict):
            raw = raw.get('tools', [])
        if not isinstance(raw, list):
            continue
        for t in raw:
            if not isinstance(t, dict):
                continue
            tools.append({
                'server_id': sid,
                'name': t.get('name'),
                'description': t.get('description'),
                'input_schema': t.get('input_schema', t.get('inputSchema')),
                'annotations': t.get('annotations'),
            })
    tools.sort(key=lambda t: (t.get('name') or ''))
    return tools

def extract_tool_names(tools: List[Dict[str, Any]]) -> List[str]:
    return sorted([t['name'] for t in tools if t.get('name')])

def extract_permission_scopes(tools: List[Dict[str, Any]]) -> Optional[str]:
    scopes = set()
    for tool in tools:
        annotations = tool.get('annotations', {})
        if annotations:
            if isinstance(annotations, str):
                try:
                    annotations = json.loads(annotations)
                except Exception:
                    pass
            if isinstance(annotations, dict):
                read_scope = annotations.get('read', [])
                write_scope = annotations.get('write', [])
                admin_scope = annotations.get('admin', [])
                for scope_list in [read_scope, write_scope, admin_scope]:
                    if isinstance(scope_list, list):
                        scopes.update(str(s) for s in scope_list)
                    elif scope_list:
                        scopes.add(str(scope_list))
    sorted_scopes = sorted(scopes)
    # No declared scopes is NOT "a server whose scopes hash to X". It is no
    # evidence about scopes at all, and auth_strength depends on the difference.
    return hash_or_absent(','.join(sorted_scopes))

def extract_combined_description(tools: List[Dict[str, Any]]) -> str:
    descriptions = []
    for tool in tools:
        desc = tool.get('description', '')
        if desc:
            descriptions.append(desc)
    return ' '.join(descriptions)

def extract_version_from_description(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r'version\s+(\d+\.\d+(?:\.\d+)?)',
        r'v(\d+\.\d+(?:\.\d+)?)',
        r'(\d+\.\d+(?:\.\d+)?)\s*$'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def fingerprint_to_dict(fp: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'tool_name_hash': fp.get('tool_name_hash', ''),
        'description_tokens': ','.join(sorted(fp.get('description_tokens', set()))),
        'permission_scope_hash': fp.get('permission_scope_hash', ''),
        'domain_fingerprint': fp.get('domain_fingerprint', ''),
        'version_string': fp.get('version_string', '')
    }

def dict_to_fingerprint(server_id: str, data: Dict[str, Any],
                        created_at: str = None) -> Dict[str, Any]:
    tokens_str = data.get('description_tokens', '')
    return {
        'server_id': server_id,
        'tool_name_hash': data.get('tool_name_hash', ''),
        'description_tokens': set(tokens_str.split(',')) if tokens_str else set(),
        'permission_scope_hash': data.get('permission_scope_hash', ''),
        'domain_fingerprint': data.get('domain_fingerprint', ''),
        'version_string': data.get('version_string', ''),
        'created_at': data.get('created_at', created_at)
    }

def ensure_fingerprints_table() -> bool:
    """Ensure mcp_fingerprints table exists. Uses ws_execute (was ws_query before fix)."""
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {FINGERPRINTS_TABLE} (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR UNIQUE NOT NULL,
        tool_name_hash VARCHAR,
        description_tokens TEXT,
        permission_scope_hash VARCHAR,
        domain_fingerprint VARCHAR,
        version_string VARCHAR,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )
    """
    try:
        return ws_execute(create_sql)
    except Exception as e:
        log.error(f"Failed to create fingerprints table: {e}")
        return False

def generate_fingerprint(server_id: str) -> Optional[Dict[str, Any]]:
    """Generate a stable behavioral fingerprint for an MCP server."""
    try:
        query = """
        SELECT server_id, name, url, description
        FROM mcp_server_registry
        WHERE server_id = ?
        """
        results = ws_query(query, [server_id])
        if not results:
            log.warning(f"Server {server_id} not found in registry")
            return None

        server_row = results[0]
        server_name = server_row[1]
        server_url = server_row[2]
        server_description = server_row[3] or ""

        tools = get_server_tools(server_id)

        tool_names = extract_tool_names(tools)
        tool_name_hash = hash_or_absent(','.join(tool_names))

        combined_desc = extract_combined_description(tools) + ' ' + server_description
        description_tokens = extract_significant_tokens(combined_desc, top_n=20)

        permission_scope_hash = extract_permission_scopes(tools)

        domain_fingerprint = extract_domain_fingerprint(server_url)
        if not domain_fingerprint:
            domain_fingerprint = ""

        version_string = extract_version_from_description(server_description)
        if not version_string:
            version_string = ""

        fingerprint = {
            'server_id': server_id,
            'tool_name_hash': tool_name_hash,
            'description_tokens': description_tokens,
            'permission_scope_hash': permission_scope_hash,
            'domain_fingerprint': domain_fingerprint,
            'version_string': version_string
        }

        fp_dict = fingerprint_to_dict(fingerprint)
        fp_dict['server_id'] = server_id

        write_success = ws_write(FINGERPRINTS_TABLE, [fp_dict])
        if write_success:
            log.info(f"Stored fingerprint for {server_id}")
        else:
            log.warning(f"Failed to store fingerprint for {server_id}")

        return fingerprint

    except Exception as e:
        log.error(f"Error generating fingerprint for {server_id}: {e}")
        return None

def compare_fingerprints(fp_a: Dict[str, Any], fp_b: Dict[str, Any]) -> Optional[float]:
    if not fp_a or not fp_b:
        return None
    required_keys = ['tool_name_hash', 'description_tokens',
                     'permission_scope_hash', 'domain_fingerprint', 'version_string']
    for key in required_keys:
        if key not in fp_a or key not in fp_b:
            return None
    tool_sim = 1.0 if fp_a['tool_name_hash'] == fp_b['tool_name_hash'] else 0.0
    tokens_a = fp_a.get('description_tokens', set())
    tokens_b = fp_b.get('description_tokens', set())
    if isinstance(tokens_a, str):
        tokens_a = set(tokens_a.split(',')) if tokens_a else set()
    if isinstance(tokens_b, str):
        tokens_b = set(tokens_b.split(',')) if tokens_b else set()
    desc_sim = compute_jaccard_similarity(tokens_a, tokens_b)
    perm_sim = 1.0 if fp_a['permission_scope_hash'] == fp_b['permission_scope_hash'] else 0.0
    domain_a = fp_a.get('domain_fingerprint', '')
    domain_b = fp_b.get('domain_fingerprint', '')
    if domain_a and domain_b:
        domain_sim = 1.0 if domain_a == domain_b else 0.0
    else:
        domain_sim = 0.5 if not domain_a and not domain_b else 0.0
    ver_a = fp_a.get('version_string', '')
    ver_b = fp_b.get('version_string', '')
    if ver_a and ver_b:
        version_sim = 1.0 if ver_a == ver_b else 0.0
    else:
        version_sim = 0.5 if not ver_a and not ver_b else 0.0
    similarity = (
        tool_sim * 0.30 +
        desc_sim * 0.25 +
        perm_sim * 0.20 +
        domain_sim * 0.15 +
        version_sim * 0.10
    )
    return round(max(0.0, min(1.0, similarity)), 4)

def detect_impersonation(server_id: str, threshold: float = 0.8) -> List[Dict[str, Any]]:
    try:
        ensure_fingerprints_table()
        query = """
        SELECT msr.server_id, msr.name,
               fp.tool_name_hash, fp.description_tokens,
               fp.permission_scope_hash, fp.domain_fingerprint,
               fp.version_string, fp.computed_at AS created_at
        FROM mcp_server_registry msr
        INNER JOIN mcp_fingerprints fp ON msr.server_id = fp.server_id
        WHERE fp.server_id != ?
        ORDER BY msr.server_id
        """
        results = ws_query(query, [server_id])
        if not results:
            return []
        current_fp = None
        query_current = """
        SELECT fp.tool_name_hash, fp.description_tokens,
               fp.permission_scope_hash, fp.domain_fingerprint,
               fp.version_string
        FROM mcp_fingerprints fp
        WHERE fp.server_id = ?
        """
        current_results = ws_query(query_current, [server_id])
        if current_results:
            row = current_results[0]
            current_fp = {
                'tool_name_hash': row[0] or '',
                'description_tokens': set(row[1].split(',')) if row[1] else set(),
                'permission_scope_hash': row[2] or '',
                'domain_fingerprint': row[3] or '',
                'version_string': row[4] or ''
            }
        if not current_fp:
            log.warning(f"No fingerprint found for {server_id}")
            return []
        impersonation_candidates = []
        for row in results:
            existing_id = row[0]
            existing_name = row[1]
            fp = {
                'tool_name_hash': row[2] or '',
                'description_tokens': set(row[3].split(',')) if row[3] else set(),
                'permission_scope_hash': row[4] or '',
                'domain_fingerprint': row[5] or '',
                'version_string': row[6] or ''
            }
            similarity = compare_fingerprints(current_fp, fp)
            if similarity is not None and similarity >= threshold:
                impersonation_candidates.append({
                    'server_id': existing_id,
                    'similarity': similarity,
                    'server_name': existing_name
                })
                log.warning(
                    f"Potential impersonation detected: {server_id} <-> {existing_id} "
                    f"(similarity: {similarity})"
                )
        impersonation_candidates.sort(key=lambda x: x['similarity'], reverse=True)
        return impersonation_candidates
    except Exception as e:
        log.error(f"Error detecting impersonation for {server_id}: {e}")
        return []

def get_all_fingerprints() -> List[Dict[str, Any]]:
    try:
        query = f"""
        SELECT server_id, tool_name_hash, description_tokens,
               permission_scope_hash, domain_fingerprint,
               version_string, created_at
        FROM {FINGERPRINTS_TABLE}
        """
        results = ws_query(query)
        return [dict_to_fingerprint(row[0], {
            'tool_name_hash': row[1],
            'description_tokens': row[2],
            'permission_scope_hash': row[3],
            'domain_fingerprint': row[4],
            'version_string': row[5],
            'created_at': row[6]
        }) for row in results]
    except Exception as e:
        log.error(f"Error fetching fingerprints: {e}")
        return []

def find_similar_servers(server_id: str, min_similarity: float = 0.5) -> List[Dict[str, Any]]:
    try:
        all_fps = get_all_fingerprints()
        if not all_fps:
            return []
        current_fp = None
        for fp in all_fps:
            if fp['server_id'] == server_id:
                current_fp = fp
                break
        if not current_fp:
            log.warning(f"No fingerprint for {server_id}")
            return []
        similar_servers = []
        for fp in all_fps:
            if fp['server_id'] == server_id:
                continue
            similarity = compare_fingerprints(current_fp, fp)
            if similarity is not None and similarity >= min_similarity:
                similar_servers.append({
                    'server_id': fp['server_id'],
                    'similarity': similarity
                })
        similar_servers.sort(key=lambda x: x['similarity'], reverse=True)
        return similar_servers
    except Exception as e:
        log.error(f"Error finding similar servers: {e}")
        return []

def run():
    """Run fingerprint generation for all registered servers."""
    log.info("Starting MCP fingerprinter for all servers")
    ensure_fingerprints_table()
    query = "SELECT server_id, name FROM mcp_server_registry ORDER BY server_id"
    results = ws_query(query)
    if not results:
        log.warning("No servers found in registry")
        return
    log.info(f"Found {len(results)} servers to fingerprint")
    for i, row in enumerate(results):
        server_id = row[0]
        server_name = row[1]
        log.info(f"[{i+1}/{len(results)}] Fingerprinting {server_id} ({server_name})")
        fp = generate_fingerprint(server_id)
        if fp:
            log.info(f"  Generated fingerprint for {server_id}")
        else:
            log.warning(f"  Failed to generate fingerprint for {server_id}")
    log.info("Fingerprinting complete")

if __name__ == "__main__":
    run()