import sqlite3
import os
from datetime import datetime

# Configuration
DB_PATH = "zo_sentinel.db"
TARGET_FINGERPRINTS = 34
MIN_DISTINCT_SCORES = 20
ENRICHMENT_VERSION = "v4"

def validate_tool_count_enrichment():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Query distinct scores for tool_count signal type
    cursor.execute("""
        SELECT DISTINCT score 
        FROM mcp_signal_enrichments 
        WHERE signal_type = 'tool_count'
    """)
    distinct_scores = [row[0] for row in cursor.fetchall()]
    count = len(distinct_scores)
    
    conn.close()
    
    print(f"Validation: Found {count} distinct scores for tool_count.")
    
    if count < MIN_DISTINCT_SCORES:
        print(f"Validation Failed: Below threshold of {MIN_DISTINCT_SCORES}. Proposing v5.")
        propose_v5_enrichment()
    else:
        print("Validation Passed: Sufficient discrimination achieved.")

def propose_v5_enrichment():
    v5_content = """
# tool_count_enrichment_v5.py
# Generated: {timestamp}
# Improved discrimination using multi-field metadata weighting:
# score = (tool_count * 0.5) + (metadata_complexity * 0.3) + (execution_frequency * 0.2)

def calculate_score(tool_count, metadata):
    complexity = len(metadata.get('tags', [])) + len(metadata.get('dependencies', []))
    freq = metadata.get('execution_frequency', 1)
    return (tool_count * 0.5) + (complexity * 0.3) + (freq * 0.2)
""".format(timestamp=datetime.now().isoformat())
    
    with open("tool_count_enrichment_v5.py", "w") as f:
        f.write(v5_content)
    print("Proposal: tool_count_enrichment_v5.py created successfully.")

if __name__ == "__main__":
    validate_tool_count_enrichment()