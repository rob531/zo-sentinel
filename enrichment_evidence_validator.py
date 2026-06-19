import sqlite3
import pandas as pd
import numpy as np

def validate_enrichment_quality(db_path="zo_sentinel.db"):
    """
    Validates enrichment module score variance per spec section 3.
    Requirements:
    1. At least 20 distinct scores per module.
    2. No module scores confined to a 10-point band.
    """
    conn = sqlite3.connect(db_path)
    query = "SELECT signal_type, enrichment_module, score FROM mcp_signal_enrichments"
    df = pd.read_sql_query(query, conn)
    conn.close()

    results = []
    grouped = df.groupby(['signal_type', 'enrichment_module'])

    for (signal_type, module), group in grouped:
        scores = group['score'].dropna()
        distinct_count = scores.nunique()
        score_range = scores.max() - scores.min()
        
        # Criteria checks
        insufficient_variance = distinct_count < 20
        failed_discrimination = score_range <= 10
        
        if insufficient_variance or failed_discrimination:
            results.append({
                "signal_type": signal_type,
                "module": module,
                "distinct_scores": distinct_count,
                "range": score_range,
                "status": "FAIL",
                "reason": f"{'Insufficient variance' if insufficient_variance else ''} {'Failed discrimination (10pt band)' if failed_discrimination else ''}"
            })

    if results:
        report = pd.DataFrame(results)
        print("--- QUALITY VALIDATION FAILURES ---")
        print(report.to_string(index=False))
        return False
    
    print("All enrichment modules passed quality validation.")
    return True

if __name__ == "__main__":
    validate_enrichment_quality()