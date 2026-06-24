import psycopg2
from psycopg2 import sql

def verify_enrichment_evidence_gate():
    # Connect to the database
    conn = psycopg2.connect(
        dbname="your_dbname",
        user="your_username",
        password="your_password",
        host="your_host"
    )
    cursor = conn.cursor()

    # Query to check evidence_blob presence and signal_type coverage
    query = sql.SQL("""
        SELECT
            signal_type,
            COUNT(*) as enrichment_count,
            COUNT(CASE WHEN evidence_blob IS NOT NULL THEN 1 END) as evidence_blob_count
        FROM
            mcp_signal_enrichments
        GROUP BY
            signal_type
    """)

    cursor.execute(query)
    results = cursor.fetchall()

    # Expected signal types
    expected_signals = {
        'signal1', 'signal2', 'signal3', 'signal4',
        'signal5', 'signal6', 'signal7', 'signal8'
    }

    # Check for missing signals
    found_signals = {result[0] for result in results}
    missing_signals = expected_signals - found_signals

    # Report results
    print("Enrichment Evidence Gate Verification Report")
    print("===========================================")
    print("\nSignal Type Coverage:")
    for result in results:
        print(f"Signal Type: {result[0]}, Enrichment Count: {result[1]}, Evidence Blob Count: {result[2]}")

    if missing_signals:
        print("\nMissing Signals:")
        for signal in missing_signals:
            print(f"Signal Type: {signal} is missing from enrichments table.")
    else:
        print("\nAll expected signals are present in the enrichments table.")

    # Close the database connection
    cursor.close()
    conn.close()

if __name__ == "__main__":
    verify_enrichment_evidence_gate()