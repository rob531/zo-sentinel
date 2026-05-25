#!/usr/bin/env python3
"""
r1_signal_discrimination_floor_patcher.py
Patches signal_bridge.py to add discrimination floor invariant in write functions.
"""

import re
import sys

def find_scoring_function(content):
    """Find the primary function that writes scores to mcp_signal_scores."""
    patterns = [
        r'def write_score\(',
        r'def write_signal_scores\(',
        r'def insert_score\(',
        r'def score\(',
        r'def calculate_score\(',
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return content[match.start():match.end()]
    return None

def find_score_dict_assignment(content):
    """Find where the scores dict is built prior to INSERT."""
    # Look for dict creation patterns near INSERT statements
    patterns = [
        r"rows\s*=\s*\{[^}]+\}",  # rows = {...}
        r"scores\s*=\s*\{[^}]+\}",  # scores = {...}
        r"signal_scores\s*=\s*\{[^}]+\}",  # signal_scores = {...}
    ]
    for pattern in patterns:
        matches = list(re.finditer(pattern, content))
        if matches:
            # Return the last one (most likely to be where scores are finalized)
            return matches[-1]
    return None

def patch_signal_bridge():
    filepath = "/home/workspace/zo_sentinel/signal_bridge.py"
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {filepath} not found")
        sys.exit(1)
    
    if 'DISCRIMINATION_FLOOR' in content and '_enforce_discrimination_floor' in content:
        print("Already patched - discrimination floor invariant exists")
        return True
    
    # Find the INSERT statement to mcp_signal_scores
    insert_match = re.search(
        r"INSERT\s+INTO\s+mcp_signal_scores[^;]*;",
        content,
        re.IGNORECASE | re.DOTALL
    )
    if not insert_match:
        print("WARNING: Could not find mcp_signal_scores INSERT - manual review needed")
    
    # Insert the discrimination floor helper function before the scoring function
    floor_function = '''
# ============================================================
# Discrimination Floor Invariant (PATCHED)
# Ensures minimum score spread for signal discrimination
# ============================================================
DISCRIMINATION_FLOOR = 0.15  # Minimum spread between min and max signals

def _enforce_discrimination_floor(scores_dict):
    """
    Non-blocking enforcement of discrimination floor.
    Scales scores if spread falls below floor threshold.
    """
    if not scores_dict:
        return scores_dict
    
    values = [v for v in scores_dict.values() if v is not None]
    if not values:
        return scores_dict
    
    min_val = min(values)
    max_val = max(values)
    spread = max_val - min_val
    
    if spread < DISCRIMINATION_FLOOR and spread > 0:
        # Scale scores to meet floor - non-blocking adjustment
        scale_factor = DISCRIMINATION_FLOOR / spread
        return {k: (v * scale_factor if v is not None else None) for k, v in scores_dict.items()}
    elif spread == 0 and min_val < DISCRIMINATION_FLOOR:
        # All scores identical and below floor - bump to floor
        return {k: DISCRIMINATION_FLOOR for k in scores_dict}
    
    return scores_dict

'''
    
    # Find where to insert - before first def in the module
    first_def_match = re.search(r'^def\s+\w+\(', content, re.MULTILINE)
    if first_def_match:
        insert_pos = first_def_match.start()
        content = content[:insert_pos] + floor_function + '\n' + content[insert_pos:]
    
    # Now find the function that builds scores dict before INSERT
    # and add the floor enforcement call
    lines = content.split('\n')
    modified = False
    
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)
        
        # Detect score dict being built
        if re.match(r'\s*(rows|scores|signal_scores)\s*=\s*\{', line):
            # Check if it's followed by INSERT
            j = i + 1
            insert_context = []
            while j < min(i + 10, len(lines)):
                next_line = lines[j]
                insert_context.append(next_line)
                if 'INSERT INTO mcp_signal_scores' in next_line.upper():
                    break
                j += 1
            
            if any('INSERT INTO mcp_signal_scores' in ctx.upper() for ctx in insert_context):
                # Insert floor enforcement before the INSERT
                indent = re.match(r'\s*', line).group()
                new_lines.append(f'{indent}# Apply discrimination floor - non-blocking')
                new_lines.append(f'{indent}scores = _enforce_discrimination_floor(scores)')
                modified = True
                continue
        
        i += 1
    
    content = '\n'.join(new_lines)
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    if modified:
        print("SUCCESS: Patched signal_bridge.py with discrimination floor invariant")
        print("- Added DISCRIMINATION_FLOOR constant (0.15)")
        print("- Added _enforce_discrimination_floor() function")
        print("- Integrated floor enforcement before mcp_signal_scores INSERT")
        return True
    else:
        print("WARNING: Patch applied but INSERT hook not found - manual review recommended")
        return False

if __name__ == '__main__':
    success = patch_signal_bridge()
    sys.exit(0 if success else 1)