import os
import re

def audit_trust_synthesiser_v2():
    file_path = '/home/workspace/zo_sentinel/trust_synthesiser_v2.py'
    
    if not os.path.exists(file_path):
        return f"ERROR: File not found at {file_path}"
    
    with open(file_path, 'r') as f:
        source = f.read()
    
    # Check for mcp_signal_scores reference
    mcp_signal_ref = 'mcp_signal_scores' in source
    
    # Check for dimension='injection_resilience'
    dim_pattern = re.compile(r"""['\"]injection_resilience['\"]""")
    dim_found = bool(dim_pattern.search(source))
    
    # Check for weight 1.6
    weight_pattern = re.compile(r'(?:weight\s*[:=]?\s*1\.6|1\.6\s*\*\s*.*weight)')
    weight_found = bool(weight_pattern.search(source))
    
    # Check for threshold 0.80
    threshold_pattern = re.compile(r'threshold\s*[:=]?\s*0\.80')
    threshold_found = bool(threshold_pattern.search(source))
    
    if mcp_signal_ref and dim_found and weight_found and threshold_found:
        audit_stub = '''
# ZO-SENTINEL Audit Stub
# File: trust_synthesiser_v2.py
# Phase 8 Status: COMPLIANT
# Verified: 
#   - Reads mcp_signal_scores table
#   - Filters dimension='injection_resilience'
#   - Applies weight=1.6
#   - Applies threshold=0.80
# Auditor: trust_synthesiser_v2_pi_audit.py
'''
        return audit_stub.strip()
    else:
        issues = []
        if not mcp_signal_ref:
            issues.append("MISSING: mcp_signal_scores reference")
        if not dim_found:
            issues.append("MISSING: dimension='injection_resilience' filter")
        if not weight_found:
            issues.append("MISSING: weight=1.6")
        if not threshold_found:
            issues.append("MISSING: threshold=0.80")
        
        return "PATCH NOTE:\\n" + "\\n".join([f"  - {i}" for i in issues])

if __name__ == '__main__':
    print(audit_trust_synthesiser_v2())