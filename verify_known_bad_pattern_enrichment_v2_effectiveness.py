import pandas as pd
from datetime import datetime

def verify_known_bad_pattern_enrichment_v2_effectiveness():
    # Query mcp_signal_scores for signal_type='known_bad_pattern'
    query = "SELECT server, score FROM mcp_signal_scores WHERE signal_type='known_bad_pattern'"
    df = pd.read_sql(query, con=engine)

    # Compute distinct score count and range spread
    distinct_scores = df['score'].nunique()
    score_range = df['score'].max() - df['score'].min()

    # Check if distinct scores < 20 or range < 20.0
    if distinct_scores < 20 or score_range < 20.0:
        print("Diagnostic failed: known_bad_pattern_enrichment_v2.py does not improve signal discrimination.")
        print(f"Distinct scores: {distinct_scores}, Score range: {score_range}")
        return False
    else:
        print("Diagnostic passed: known_bad_pattern_enrichment_v2.py improves signal discrimination.")
        print(f"Distinct scores: {distinct_scores}, Score range: {score_range}")
        return True

if __name__ == "__main__":
    verify_known_bad_pattern_enrichment_v2_effectiveness()