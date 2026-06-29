import psycopg2
from psycopg2 import sql

def verify_enrichment_wiring():
    # List of recently built enrichment modules to verify
    built_enrichments = [
        'known_bad_pattern_enrichment_v2',
        'tool_count_enrichment_v2',
        'tool_description_safety_enrichment',
        'temporal_stability_enrichment_v2',
        'permission_scope_enrichment_v2'
    ]

    # Connect to the database
    conn = psycopg2.connect(
        dbname='your_db_name',
        user='your_db_user',
        password='your_db_password',
        host='your_db_host'
    )
    cur = conn.cursor()

    # Query to get distinct signal_types and count rows per enrichment
    query = sql.SQL("""
        SELECT
            enrichment_name,
            COUNT(*) as row_count
        FROM
            mcp_signal_enrichments
        GROUP BY
            enrichment_name
    """)

    cur.execute(query)
    results = cur.fetchall()

    # Create a dictionary of enrichment names to row counts
    enrichment_counts = {enrichment: count for enrichment, count in results}

    # Check which enrichments have 0 rows
    missing_enrichments = []
    for enrichment in built_enrichments:
        if enrichment not in enrichment_counts or enrichment_counts[enrichment] == 0:
            missing_enrichments.append(enrichment)

    # Report results
    if not missing_enrichments:
        print("PASS: All built enrichments have evidence rows.")
    else:
        print(f"FAIL: The following enrichments are not properly wired or have no rows: {', '.join(missing_enrichments)}")

    # Close the database connection
    cur.close()
    conn.close()

if __name__ == "__main__":
    verify_enrichment_wiring()