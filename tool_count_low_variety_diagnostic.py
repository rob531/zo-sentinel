import pandas as pd
from sqlalchemy import create_engine

# Database connection
engine = create_engine('postgresql://user:password@host:port/database')

# Query to check tool_count signal cardinality
query = """
SELECT score_value, COUNT(*) as count
FROM mcp_signal_scores
WHERE signal_type='tool_count'
GROUP BY score_value
ORDER BY score_value;
"""

# Execute query and load results into a DataFrame
df = pd.read_sql(query, engine)

# Check if tool_count values cluster into ≤3 buckets
if len(df) <= 3:
    print("tool_count signal has low cardinality and contributes nothing.")
else:
    print("tool_count signal has sufficient cardinality.")

# Propose tool_count_diversity_enrichment_v4.py
tool_count_diversity_enrichment_v4 = """
import pandas as pd
from sqlalchemy import create_engine

# Database connection
engine = create_engine('postgresql://user:password@host:port/database')

# Query to fetch additional metadata fields
query = \"""
SELECT s.server_id, s.tool_count, m.registry_source, m.age_days, m.download_count, m.publisher_verified, m.stars
FROM mcp_signal_scores s
JOIN metadata m ON s.server_id = m.server_id
WHERE s.signal_type='tool_count'
AND s.server_id NOT IN (SELECT server_id FROM quarantine_list)
AND s.last_error != 'failed cohort_3_n7';
\"""

# Execute query and load results into a DataFrame
df = pd.read_sql(query, engine)

# Enrich tool_count with additional metadata fields
df['tool_count_diversity'] = df['tool_count'] * df['registry_source'].astype('category').cat.codes * df['age_days'] * df['download_count'] * df['publisher_verified'] * df['stars']

# Save the enriched DataFrame to a CSV file
df.to_csv('tool_count_diversity_enriched.csv', index=False)
"""