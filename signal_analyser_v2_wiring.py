import os
import re
from datetime import datetime, timezone

SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'
OUTPUT_PATH = '/home/workspace/zo_sentinel/signal_analyser_v2_wiring.py'
LOG_FILE = '/home/workspace/logs/signal_analyser_v2_wiring.log'

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def read_source(path: str) -> str:
    with open(path, 'r') as f:
        return f.read()

def write_source(path: str, content: str) -> None:
    with open(path, 'w') as f:
        f.write(content)
    log(f"Wrote patched source to {path}")

def backup_path(path: str) -> str:
    ts = utc_now_iso().replace(':', '-').replace('+00:00', 'Z')
    bp = f"{path}.backup_{ts}"
    with open(path, 'r') as src:
        with open(bp, 'w') as dst:
            dst.write(src.read())
    log(f"Backed up to {bp}")
    return bp

SIGNAL_ANALYSER_V2_IMPORT_BLOCK = '''# ── v2 enrichment module imports ─────────────────────────────────────────────
import importlib.util
import sys as _sys

_sys.path.insert(0, '/home/workspace/zo_sentinel')

_v2_modules = {}

def _load_v2_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _v2_modules[name] = mod

_load_v2_module("temporal_stability_enrichment_v2",
                 "/home/workspace/zo_sentinel/temporal_stability_enrichment_v2.py")
_load_v2_module("permission_scope_enrichment_v2",
                 "/home/workspace/zo_sentinel/permission_scope_enrichment_v2.py")
_load_v2_module("tool_description_safety_enrichment_v2",
                 "/home/workspace/zo_sentinel/tool_description_safety_enrichment_v2.py")

def _call_v2_score(module_name: str, metadata: dict, legacy_score: float = 50.0) -> tuple[float, dict]:
    mod = _v2_modules.get(module_name)
    if mod and hasattr(mod, 'compute_score'):
        score, evidence = mod.compute_score(metadata)
        evidence['source'] = module_name
        evidence['legacy_blend'] = legacy_score
        return score, evidence
    return legacy_score, {'source': module_name, 'error': 'module not loaded', 'score': legacy_score}

'''

LEGACY_SECTION_MARKER = '# ── LEGACY ENRICHMENT CALLS BELOW ──'

ENRICHMENT_WRAPPER_CODE = '''
# ── v2 enrichment scoring ────────────────────────────────────────────────────
def _score_with_v2_enrichments(server: dict, legacy_trust_score: float = 50.0) -> dict:
    """
    Replace inline signal computations with v2 enrichment modules.
    Each dimension returns (score, evidence); scores are blended 70/30 v2/legacy.
    """
    evidence_blob = {}
    total_weight = 0.0
    weighted_sum = 0.0

    metadata = {
        'name': server.get('name', ''),
        'description': server.get('description', ''),
        'registry_source': server.get('registry_source', ''),
        'created_at': server.get('created_at') or server.get('first_seen'),
        'trust_score': legacy_trust_score,
        'download_count': server.get('download_count') or 0,
        'dependency_count': server.get('dependency_count') or 0,
        'publisher_verified': server.get('publisher_verified', False),
        'stars': server.get('stars') or 0,
        'tool_names': server.get('tool_names') or [],
        'tool_count': server.get('tool_count') or 0,
        'permission_list': server.get('permission_list') or [],
        'tool_descriptions': server.get('tool_descriptions') or [],
    }

    v2_configs = [
        ('temporal_stability_enrichment_v2', 'temporal_stability', 0.20),
        ('permission_scope_enrichment_v2', 'permission_scope', 0.25),
        ('tool_description_safety_enrichment_v2', 'tool_description_safety', 0.20),
    ]

    for module_name, signal_name, weight in v2_configs:
        score, ev = _call_v2_score(module_name, metadata, legacy_trust_score)
        evidence_blob[signal_name] = ev
        weighted_sum += score * weight
        total_weight += weight

    blend_factor = 0.70
    final_score = (weighted_sum / total_weight) * blend_factor + legacy_trust_score * (1 - blend_factor) if total_weight > 0 else legacy_trust_score
    evidence_blob['composite'] = {
        'v2_score': weighted_sum / total_weight if total_weight > 0 else legacy_trust_score,
        'legacy_blend': legacy_trust_score,
        'final_blended': final_score,
        'blend_factor': blend_factor,
    }
    return {'score': final_score, 'evidence': evidence_blob}

'''

def already_wired(content: str) -> bool:
    return 'temporal_stability_enrichment_v2' in content and '_score_with_v2_enrichments' in content

def patch_signal_analyser(content: str) -> str:
    if already_wired(content):
        log("signal_analyser.py already contains v2 wiring — no-op")
        return content

    insert_pos = 0
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped and not stripped.startswith('#') and not stripped.startswith('"""') and not stripped.startswith("'''"):
            insert_pos = i
            break

    patched_lines = lines[:insert_pos] + [SIGNAL_ANALYSER_V2_IMPORT_BLOCK] + lines[insert_pos:]

    result = '\n'.join(patched_lines)

    enrich_marker_idx = result.find(LEGACY_SECTION_MARKER)
    if enrich_marker_idx == -1:
        result += ENRICHMENT_WRAPPER_CODE
    else:
        result = result[:enrich_marker_idx] + ENRICHMENT_WRAPPER_CODE + result[enrich_marker_idx:]

    scan_func_pattern = r'(def scan_server\(.*?\).*?)(legacy trust|inline|OLD|DEPRECATED)'
    result = re.sub(scan_func_pattern, r'\1_v2', result, flags=re.IGNORECASE | re.DOTALL)

    return result

def wire_signal_analyser_v2() -> None:
    log("Starting signal_analyser v2 wiring patch")
    if not os.path.exists(SIGNAL_ANALYSER_PATH):
        log(f"ERROR: signal_analyser.py not found at {SIGNAL_ANALYSER_PATH}")
        return

    content = read_source(SIGNAL_ANALYSER_PATH)
    backup_path(SIGNAL_ANALYSER_PATH)
    patched = patch_signal_analyser(content)
    write_source(SIGNAL_ANALYSER_PATH, patched)
    log("signal_analyser.py patched successfully")

def run() -> None:
    wire_signal_analyser_v2()

if __name__ == '__main__':
    run()