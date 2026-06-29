import pandas as pd
from datetime import datetime

def diagnose_tool_count_enrichment_low_discrimination():
    # Load mcp_signal_scores for signal_type='tool_count'
    mcp_signal_scores = pd.read_csv('mcp_signal_scores.csv')
    tool_count_scores = mcp_signal_scores[mcp_signal_scores['signal_type'] == 'tool_count']

    # Compute distinct values for each metadata field
    distinct_values = {
        'registry_source': tool_count_scores['registry_source'].nunique(),
        'age_days': tool_count_scores['age_days'].nunique(),
        'download_count': tool_count_scores['download_count'].nunique(),
        'dependency_count': tool_count_scores['dependency_count'].nunique(),
        'publisher_verified': tool_count_scores['publisher_verified'].nunique(),
        'stars': tool_count_scores['stars'].nunique()
    }

    # Analyze tool_count_enrichment_v2.py to check if compute_score() reads multiple metadata fields
    with open('tool_count_enrichment_v2.py', 'r') as file:
        content = file.read()

    # Check if compute_score() reads multiple metadata fields
    metadata_fields = ['registry_source', 'age_days', 'download_count', 'dependency_count', 'publisher_verified', 'stars']
    fields_read = [field for field in metadata_fields if field in content]

    # Output the analysis
    analysis = {
        'distinct_values': distinct_values,
        'fields_read_in_compute_score': fields_read,
        'last_modified': datetime(2026, 6, 29, 10, 36, 55)
    }

    return analysis

# Run the diagnosis
diagnosis = diagnose_tool_count_enrichment_low_discrimination()
print(diagnosis)