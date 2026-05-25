import hashlib
import math
import re
from typing import Dict, Tuple, List, Any

SIGNAL_NAME = "permission_scope"
VERSION = "v3"
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

def score_permission_scope(scope: str) -> float:
    if not scope:
        return 0.0
    scope_lower = scope.lower().strip()
    if scope_lower == 'none':
        return 100.0
    elif scope_lower == 'read':
        return 85.0
    elif scope_lower == 'read_write':
        return 55.0
    elif scope_lower == 'write':
        return 30.0
    elif scope_lower == 'admin':
        return 5.0
    elif scope_lower == 'filesystem':
        return 10.0
    elif scope_lower == 'network':
        return 15.0
    else:
        hash_val = hash_string(scope) * 50.0
        return max(5.0, 50.0 + hash_val - 25.0)

def score_requested_permissions(perms: List[str]) -> float:
    if not perms:
        return 100.0
    if not isinstance(perms, list):
        return 50.0
    perm_count = len(perms)
    if perm_count == 0:
        return 100.0
    elif perm_count <= 3:
        return 85.0
    elif perm_count <= 10:
        return 65.0
    elif perm_count <= 25:
        return 40.0
    elif perm_count <= 50:
        return 20.0
    else:
        return 5.0

def score_dangerous_permissions(dangerous_count: int) -> float:
    if dangerous_count <= 0:
        return 100.0
    elif dangerous_count == 1:
        return 70.0
    elif dangerous_count == 2:
        return 50.0
    elif dangerous_count <= 5:
        return 30.0
    elif dangerous_count <= 10:
        return 15.0
    else:
        return 5.0

def score_permission_granularity(granularity: int) -> float:
    if granularity <= 0:
        return 10.0
    elif granularity == 1:
        return 25.0
    elif granularity == 2:
        return 45.0
    elif granularity == 3:
        return 65.0
    elif granularity == 4:
        return 80.0
    elif granularity >= 5:
        return 95.0
    return 50.0

def score_denial_rate(rate: float) -> float:
    if rate <= 0.0:
        return 100.0
    elif rate < 0.05:
        return 90.0
    elif rate < 0.10:
        return 75.0
    elif rate < 0.20:
        return 55.0
    elif rate < 0.35:
        return 35.0
    elif rate < 0.50:
        return 20.0
    else:
        return 5.0

def score_registry_source(source: str) -> float:
    if not source:
        return 30.0
    source_lower = source.lower().strip()
    if 'npm' in source_lower and 'official' in source_lower:
        return 95.0
    elif 'npm' in source_lower:
        return 70.0
    elif 'github' in source_lower:
        return 65.0
    elif 'pypi' in source_lower:
        return 70.0
    elif ' Smithery' in source_lower or 'smithery' in source_lower:
        return 50.0
    elif 'OpenMCP' in source_lower or 'openmcp' in source_lower:
        return 60.0
    elif 'ChatGPT' in source_lower or 'chatgpt' in source_lower:
        return 55.0
    elif 'npm' in source_lower and ('beta' in source_lower or 'alpha' in source_lower):
        return 35.0
    elif 'github' in source_lower and ('unofficial' in source_lower or 'fork' in source_lower):
        return 40.0
    else:
        hash_val = hash_string(source) * 40.0
        return max(15.0, 30.0 + hash_val - 20.0)

def score_age_days(age_days: int) -> float:
    if age_days <= 0:
        return 20.0
    elif age_days < 30:
        return 25.0
    elif age_days < 90:
        return 45.0
    elif age_days < 180:
        return 60.0
    elif age_days < 365:
        return 75.0
    elif age_days < 730:
        return 85.0
    else:
        return 95.0

def score_download_count(count: int) -> float:
    return log_normalize(float(count), scale=100.0)

def score_dependency_count(count: int) -> float:
    if count <= 0:
        return 80.0
    elif count <= 3:
        return 90.0
    elif count <= 10:
        return 75.0
    elif count <= 30:
        return 55.0
    elif count <= 80:
        return 35.0
    else:
        return 15.0

def score_publisher_verified(verified: Any) -> float:
    if verified is None:
        return 40.0
    if isinstance(verified, bool):
        return 95.0 if verified else 30.0
    if isinstance(verified, str):
        vl = verified.lower().strip()
        if vl in ('true', '1', 'yes', 'verified'):
            return 95.0
        elif vl in ('false', '0', 'no'):
            return 30.0
    try:
        return 95.0 if float(verified) > 0 else 30.0
    except (ValueError, TypeError):
        return 40.0

def score_stars(stars: Any) -> float:
    if stars is None:
        return 30.0
    try:
        stars_val = float(stars) if not isinstance(stars, bool) else (1.0 if stars else 0.0)
    except (ValueError, TypeError):
        return 30.0
    if stars_val <= 0:
        return 15.0
    elif stars_val < 5:
        return 25.0
    elif stars_val < 20:
        return 40.0
    elif stars_val < 100:
        return 60.0
    elif stars_val < 500:
        return 75.0
    elif stars_val < 2000:
        return 88.0
    else:
        return 97.0

def score_tool_count(count: int) -> float:
    if count <= 0:
        return 50.0
    elif count == 1:
        return 80.0
    elif count <= 5:
        return 70.0
    elif count <= 15:
        return 55.0
    elif count <= 40:
        return 40.0
    elif count <= 100:
        return 25.0
    else:
        return 10.0

def score_permission_type_distribution(perms: List[str]) -> float:
    if not perms:
        return 100.0
    if not isinstance(perms, list):
        perms = [str(perms)]
    type_counts = {'read': 0, 'write': 0, 'delete': 0, 'admin': 0, 'network': 0, 'filesystem': 0, 'exec': 0, 'env': 0}
    for p in perms:
        p_lower = p.lower()
        if 'read' in p_lower or 'get' in p_lower or 'list' in p_lower:
            type_counts['read'] += 1
        if 'write' in p_lower or 'create' in p_lower or 'update' in p_lower or 'post' in p_lower:
            type_counts['write'] += 1
        if 'delete' in p_lower or 'remove' in p_lower:
            type_counts['delete'] += 1
        if 'admin' in p_lower or 'root' in p_lower or 'sudo' in p_lower:
            type_counts['admin'] += 1
        if 'http' in p_lower or 'fetch' in p_lower or 'request' in p_lower or 'network' in p_lower:
            type_counts['network'] += 1
        if 'file' in p_lower or 'read' in p_lower or 'write' in p_lower or 'path' in p_lower:
            type_counts['filesystem'] += 1
        if 'exec' in p_lower or 'spawn' in p_lower or 'run' in p_lower:
            type_counts['exec'] += 1
        if 'env' in p_lower or 'secret' in p_lower or 'key' in p_lower or 'token' in p_lower:
            type_counts['env'] += 1
    unique_types = sum(1 for v in type_counts.values() if v > 0)
    if unique_types == 0:
        return 100.0
    elif unique_types == 1:
        return 85.0
    elif unique_types == 2:
        return 65.0
    elif unique_types == 3:
        return 45.0
    elif unique_types <= 5:
        return 25.0
    else:
        return 10.0

def score_permission_name_entropy(perms: List[str]) -> float:
    if not perms:
        return 100.0
    if not isinstance(perms, list):
        return 50.0
    if len(perms) <= 1:
        return 90.0
    normalized = [normalize_permission_name(p) for p in perms]
    unique = len(set(normalized))
    total = len(normalized)
    entropy_ratio = unique / total if total > 0 else 1.0
    if entropy_ratio >= 0.9 and total > 10:
        return 15.0
    elif entropy_ratio >= 0.7:
        return 40.0
    elif entropy_ratio >= 0.5:
        return 60.0
    else:
        return 80.0

def score_optional_vs_required_ratio(required: List[str], optional: List[str]) -> float:
    req_count = len(required) if isinstance(required, list) else 0
    opt_count = len(optional) if isinstance(optional, list) else 0
    if req_count == 0 and opt_count == 0:
        return 100.0
    if req_count == 0 and opt_count > 0:
        return 95.0
    if opt_count == 0:
        return 40.0
    ratio = opt_count / (req_count + opt_count)
    if ratio >= 0.8:
        return 90.0
    elif ratio >= 0.5:
        return 70.0
    elif ratio >= 0.2:
        return 50.0
    else:
        return 25.0

def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    if not metadata:
        return 0.0, {"error": "empty metadata", "score": 0.0}

    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "partial_scores": {},
        "weights": {}
    }

    permission_scope = metadata.get('permission_scope', '')
    requested_permissions = metadata.get('requested_permissions', [])
    optional_permissions = metadata.get('optional_permissions', [])
    dangerous_permission_count = metadata.get('dangerous_permission_count', 0)
    permission_granularity = metadata.get('permission_granularity', 0)
    permission_denial_rate = metadata.get('permission_denial_rate', 0.0)

    registry_source = metadata.get('registry_source', '')
    age_days = metadata.get('age_days', 0)
    download_count = metadata.get('download_count', 0)
    dependency_count = metadata.get('dependency_count', 0)
    publisher_verified = metadata.get('publisher_verified', None)
    stars = metadata.get('stars', None)
    tool_count = metadata.get('tool_count', 0)

    if isinstance(requested_permissions, str):
        requested_permissions = [p.strip() for p in requested_permissions.split(',') if p.strip()]
    if isinstance(optional_permissions, str):
        optional_permissions = [p.strip() for p in optional_permissions.split(',') if p.strip()]

    s_scope = score_permission_scope(permission_scope)
    s_req_perms = score_requested_permissions(requested_permissions)
    s_dangerous = score_dangerous_permissions(dangerous_permission_count)
    s_granularity = score_permission_granularity(permission_granularity)
    s_denial = score_denial_rate(permission_denial_rate)
    s_source = score_registry_source(registry_source)
    s_age = score_age_days(age_days)
    s_downloads = score_download_count(download_count)
    s_deps = score_dependency_count(dependency_count)
    s_verified = score_publisher_verified(publisher_verified)
    s_stars = score_stars(stars)
    s_tools = score_tool_count(tool_count)
    s_type_dist = score_permission_type_distribution(requested_permissions)
    s_name_entropy = score_permission_name_entropy(requested_permissions)
    s_opt_ratio = score_optional_vs_required_ratio(requested_permissions, optional_permissions)

    evidence["partial_scores"]["permission_scope_score"] = round(s_scope, 4)
    evidence["partial_scores"]["requested_permissions_score"] = round(s_req_perms, 4)
    evidence["partial_scores"]["dangerous_permission_score"] = round(s_dangerous, 4)
    evidence["partial_scores"]["permission_granularity_score"] = round(s_granularity, 4)
    evidence["partial_scores"]["denial_rate_score"] = round(s_denial, 4)
    evidence["partial_scores"]["registry_source_score"] = round(s_source, 4)
    evidence["partial_scores"]["age_days_score"] = round(s_age, 4)
    evidence["partial_scores"]["download_count_score"] = round(s_downloads, 4)
    evidence["partial_scores"]["dependency_count_score"] = round(s_deps, 4)
    evidence["partial_scores"]["publisher_verified_score"] = round(s_verified, 4)
    evidence["partial_scores"]["stars_score"] = round(s_stars, 4)
    evidence["partial_scores"]["tool_count_score"] = round(s_tools, 4)
    evidence["partial_scores"]["permission_type_distribution_score"] = round(s_type_dist, 4)
    evidence["partial_scores"]["permission_name_entropy_score"] = round(s_name_entropy, 4)
    evidence["partial_scores"]["optional_required_ratio_score"] = round(s_opt_ratio, 4)

    w_scope = 0.12
    w_req_perms = 0.10
    w_dangerous = 0.10
    w_granularity = 0.08
    w_denial = 0.07
    w_source = 0.08
    w_age = 0.07
    w_downloads = 0.06
    w_deps = 0.06
    w_verified = 0.05
    w_stars = 0.05
    w_tools = 0.05
    w_type_dist = 0.05
    w_name_entropy = 0.03
    w_opt_ratio = 0.03

    raw_score = (
        s_scope * w_scope +
        s_req_perms * w_req_perms +
        s_dangerous * w_dangerous +
        s_granularity * w_granularity +
        s_denial * w_denial +
        s_source * w_source +
        s_age * w_age +
        s_downloads * w_downloads +
        s_deps * w_deps +
        s_verified * w_verified +
        s_stars * w_stars +
        s_tools * w_tools +
        s_type_dist * w_type_dist +
        s_name_entropy * w_name_entropy +
        s_opt_ratio * w_opt_ratio
    )

    evidence["weights"]["permission_scope"] = w_scope
    evidence["weights"]["requested_permissions"] = w_req_perms
    evidence["weights"]["dangerous_permission"] = w_dangerous
    evidence["weights"]["permission_granularity"] = w_granularity
    evidence["weights"]["denial_rate"] = w_denial
    evidence["weights"]["registry_source"] = w_source
    evidence["weights"]["age_days"] = w_age
    evidence["weights"]["download_count"] = w_downloads
    evidence["weights"]["dependency_count"] = w_deps
    evidence["weights"]["publisher_verified"] = w_verified
    evidence["weights"]["stars"] = w_stars
    evidence["weights"]["tool_count"] = w_tools
    evidence["weights"]["permission_type_distribution"] = w_type_dist
    evidence["weights"]["permission_name_entropy"] = w_name_entropy
    evidence["weights"]["optional_required_ratio"] = w_opt_ratio

    final_score = round(min(100.0, max(0.0, raw_score)), 4)
    evidence["raw_score"] = round(raw_score, 4)
    evidence["final_score"] = final_score

    return final_score, evidence