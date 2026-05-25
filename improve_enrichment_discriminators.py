import subprocess
import sys
import os

SCRIPT_DIR = '/home/workspace/zo_sentinel'

def read_file(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except:
        return None

temporal_path = os.path.join(SCRIPT_DIR, 'temporal_stability_enrichment_v2.py')
tool_desc_path = os.path.join(SCRIPT_DIR, 'tool_description_safety_enrichment.py')
permission_path = os.path.join(SCRIPT_DIR, 'permission_scope_enrichment.py')

print("=== Inspecting existing enrichments ===")
for name, path in [('temporal_stability_enrichment_v2', temporal_path),
                   ('tool_description_safety_enrichment', tool_desc_path),
                   ('permission_scope_enrichment', permission_path)]:
    content = read_file(path)
    if content:
        print(f"\n--- {name} ---")
        print(f"File size: {len(content)} chars")
        if 'compute_score' in content:
            print("Has compute_score function")
        if 'def sigmoid' in content:
            print("Has sigmoid function")
        if 'def softmax' in content or 'softmax_weight' in content:
            print("Has softmax weighting")
        print("First 1500 chars:")
        print(content[:1500])
    else:
        print(f"Could not read {path}")

print("\n=== Checking for other enrichment versions ===")
for f in os.listdir(SCRIPT_DIR):
    if 'enrichment' in f.lower() and f.endswith('.py'):
        print(f"  {f}")
        content = read_file(os.path.join(SCRIPT_DIR, f))
        if content and 'compute_score' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'compute_score' in line and not line.strip().startswith('#'):
                    print(f"    Line {i}: {line[:120]}")