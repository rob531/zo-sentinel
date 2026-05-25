import hashlib
import math
from typing import Dict, Tuple, List, Any

SIGNAL_NAME = "permission_scope"
VERSION = "v2"
MAX_SCORE = 100.0

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def softmax_weight(values: List[float], temperature: float = 1.0) -> float:
    if not values:
        return 0.0
    exp_vals = [math.exp(v / temperature) for v in values]
    total = sum(exp_vals)
    if total == 0:
        return 0.0
    return exp_vals[-1] / total

def log_normalize(value: float, scale: float = 100.0) -> float:
    if value <= 0:
        return 0.0
    return min(100.0, scale * (1.0 - (1.0 / (1.0 + math.log1p(value)))))

def hash_string(s: str) -> float:
    h = hashlib.sha256(s.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xffffffffffffff

def normalize_permission_name(perm: str) -> str:
    if not perm:
        return ""
    return perm.lower().strip().replace('.', '_')

def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    if not metadata:
        return 0.0, {"error": "empty metadata", "score": 0.0}

    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "partial_scores": {},
        "weights": {}
    }

    partials = {}

    permission_scope = metadata.get('permission_scope', '')
    requested_permissions = metadata.get('requested_permissions', [])
    optional_permissions = metadata.get('optional_permissions', [])
    dangerous_permission_count = metadata.get('dangerous_permission_count', 0)
    permission_granularity = metadata.get('permission_granularity', 0)
    permission_denial_rate = metadata.get('permission_denial_rate', 0.0)

    if isinstance(requested_permissions, str):
        requested_permissions = [p.strip() for p in requested_permissions.split(',') if p.strip()]
    if isinstance(optional_permissions, str):
        optional_permissions = [p.strip() for p in optional_permissions.split(',') if p.strip()]

    if permission_scope == 'read':
        scope_score = 10.0
    elif permission_scope == 'write':
        scope_score = 20.0
    elif permission_scope == 'read_write':
        scope_score = 30.0
    elif permission_scope == 'admin':
        scope_score = 60.0
    elif permission_scope == 'system':
        scope_score = 85.0
    else:
        scope_score = 40.0

    partials['scope_score'] = scope_score
    evidence['partial_scores']['scope_score'] = scope_score

    total_perms = len(requested_permissions) + len(optional_permissions)
    perm_count_score = log_normalize(total_perms, scale=20.0)
    partials['perm_count_score'] = perm_count_score
    evidence['partial_scores']['perm_count_score'] = round(perm_count_score, 2)

    dangerous_score = 0.0
    if dangerous_permission_count > 0:
        dangerous_score = min(25.0, dangerous_permission_count * 5.0)
    partials['dangerous_score'] = dangerous_score
    evidence['partial_scores']['dangerous_score'] = round(dangerous_score, 2)

    granularity_score = 0.0
    if isinstance(permission_granularity, (int, float)):
        if permission_granularity >= 10:
            granularity_score = 20.0
        elif permission_granularity >= 5:
            granularity_score = 15.0
        elif permission_granularity >= 2:
            granularity_score = 10.0
        else:
            granularity_score = 5.0
    partials['granularity_score'] = granularity_score
    evidence['partial_scores']['granularity_score'] = round(granularity_score, 2)

    denial_score = 0.0
    if isinstance(permission_denial_rate, (int, float)):
        if permission_denial_rate >= 0.5:
            denial_score = 15.0
        elif permission_denial_rate >= 0.3:
            denial_score = 10.0
        elif permission_denial_rate >= 0.1:
            denial_score = 5.0
        else:
            denial_score = 2.0
    partials['denial_score'] = denial_score
    evidence['partial_scores']['denial_score'] = round(denial_score, 2)

    optional_ratio = 0.0
    if total_perms > 0:
        optional_ratio = len(optional_permissions) / total_perms
    optional_score = optional_ratio * 15.0
    partials['optional_score'] = optional_score
    evidence['partial_scores']['optional_score'] = round(optional_score, 2)

    entropy_score = 0.0
    all_perms = list(set(requested_permissions + optional_permissions))
    if len(all_perms) > 1:
        perm_types = {}
        for p in all_perms:
            normalized = normalize_permission_name(p)
            category = 'read'
            if any(kw in normalized for kw in ['write', 'create', 'delete', 'update', 'modify']):
                category = 'write'
            if any(kw in normalized for kw in ['admin', 'manage', 'configure', 'system']):
                category = 'admin'
            if any(kw in normalized for kw in ['dangerous', 'camera', 'location', 'microphone', 'contacts']):
                category = 'sensitive'
            perm_types[category] = perm_types.get(category, 0) + 1
        if len(perm_types) > 1:
            entropy_score = min(10.0, len(perm_types) * 3.0)
    partials['entropy_score'] = entropy_score
    evidence['partial_scores']['entropy_score'] = round(entropy_score, 2)

    sensitive_perms = [p for p in all_perms if any(kw in normalize_permission_name(p) for kw in ['camera', 'location', 'microphone', 'contacts', 'sms', 'call', 'storage', 'filesystem'])]
    sensitive_score = min(15.0, len(sensitive_perms) * 3.0)
    partials['sensitive_score'] = sensitive_score
    evidence['partial_scores']['sensitive_score'] = round(sensitive_score, 2)

    if dangerous_permission_count > 0 and len(all_perms) > 0:
        dangerous_ratio = dangerous_permission_count / len(all_perms)
        ratio_score = dangerous_ratio * 10.0
    else:
        ratio_score = 0.0
    partials['ratio_score'] = ratio_score
    evidence['partial_scores']['ratio_score'] = round(ratio_score, 2)

    raw_total = scope_score + perm_count_score + dangerous_score + granularity_score + denial_score + optional_score + entropy_score + sensitive_score + ratio_score

    score = min(MAX_SCORE, raw_total)

    evidence['weights'] = {
        'scope': 1.0,
        'count': 1.0,
        'dangerous': 1.0,
        'granularity': 1.0,
        'denial': 1.0,
        'optional': 1.0,
        'entropy': 1.0,
        'sensitive': 1.0,
        'ratio': 1.0
    }

    evidence['metadata_fields_used'] = [
        'permission_scope',
        'requested_permissions',
        'optional_permissions',
        'dangerous_permission_count',
        'permission_granularity',
        'permission_denial_rate'
    ]
    evidence['metadata_fields_found'] = {
        'permission_scope': permission_scope if permission_scope else None,
        'requested_permissions_count': len(requested_permissions),
        'optional_permissions_count': len(optional_permissions),
        'dangerous_permission_count': dangerous_permission_count,
        'permission_granularity': permission_granularity,
        'permission_denial_rate': permission_denial_rate
    }
    evidence['final_score'] = round(score, 4)

    return round(score, 2), evidence

if __name__ == '__main__':
    test_metadata = {
        'permission_scope': 'read_write',
        'requested_permissions': ['read', 'write', 'delete', 'camera', 'location'],
        'optional_permissions': ['microphone', 'contacts'],
        'dangerous_permission_count': 2,
        'permission_granularity': 8,
        'permission_denial_rate': 0.15
    }
    score, evidence = compute_score(test_metadata)
    print(f"Score: {score}")
    print(f"Evidence: {evidence}")