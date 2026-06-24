import json
import psycopg2
from psycopg2.extras import RealDictCursor

def verify_enrichment_evidence_shape_compliance():
    # Connect to the database
    conn = psycopg2.connect(
        dbname='your_dbname',
        user='your_username',
        password='your_password',
        host='your_host',
        port='your_port'
    )
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Query recent rows from mcp_signal_enrichments
    cursor.execute("""
        SELECT id, signal_type, evidence_blob
        FROM mcp_signal_enrichments
        WHERE created_at > NOW() - INTERVAL '7 days'
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()

    # Define the expected structure
    expected_structure = {
        "signal_type": str,
        "confidence": float,
        "evidence_blob": dict
    }

    # List to store non-conforming rows
    non_conforming_rows = []

    # Validate each row
    for row in rows:
        evidence_blob = json.loads(row['evidence_blob'])

        # Check if the evidence_blob matches the expected structure
        if not all(key in evidence_blob and isinstance(evidence_blob[key], expected_structure[key]) for key in expected_structure):
            non_conforming_rows.append({
                'id': row['id'],
                'signal_type': row['signal_type'],
                'actual_blob_shape': evidence_blob
            })

    # Close the database connection
    cursor.close()
    conn.close()

    # Report violations
    if non_conforming_rows:
        print("Non-conforming rows found:")
        for row in non_conforming_rows:
            print(f"ID: {row['id']}, Signal Type: {row['signal_type']}, Actual Blob Shape: {row['actual_blob_shape']}")
    else:
        print("All rows conform to the expected structure.")

if __name__ == "__main__":
    verify_enrichment_evidence_shape_compliance()