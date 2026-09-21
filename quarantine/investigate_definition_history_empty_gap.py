# investigate_definition_history_empty_gap.py

import psycopg2
from psycopg2 import sql

def investigate_definition_history_gap():
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'database': 'zo_sentinel',
        'user': 'postgres',
        'password': 'your_password_here'
    }

    findings = []
    status = "PASS"

    try:
        # Connect to the database
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()

        # 1. Check if mcp_definition_history table exists and has correct structure
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'mcp_definition_history'
            ORDER BY ordinal_position;
        """)
        columns = cursor.fetchall()

        if not columns:
            findings.append("FAIL: mcp_definition_history table does not exist or is empty")
            status = "FAIL"
        else:
            # Check for required columns
            required_columns = ['id', 'mcp_id', 'definition', 'timestamp', 'source']
            column_names = [col[0] for col in columns]
            missing_columns = [col for col in required_columns if col not in column_names]

            if missing_columns:
                findings.append(f"FAIL: mcp_definition_history table is missing required columns: {', '.join(missing_columns)}")
                status = "FAIL"
            else:
                findings.append("PASS: mcp_definition_history table has correct structure")

        # 2. Check if signal_analyser is producing data that should trigger definition_history records
        cursor.execute("""
            SELECT COUNT(*)
            FROM signal_analyser_output
            WHERE analysis_result = 'new_definition'
            AND processed_at > NOW() - INTERVAL '1 day';
        """)
        new_definitions = cursor.fetchone()[0]

        if new_definitions == 0:
            findings.append("INFO: No new definitions detected in signal_analyser output in last 24 hours")
        else:
            findings.append(f"INFO: Found {new_definitions} new definitions in signal_analyser output in last 24 hours")

        # 3. Check if trust_synthesiser is processing these signals
        cursor.execute("""
            SELECT COUNT(*)
            FROM trust_synthesiser_output
            WHERE action = 'update_definition'
            AND processed_at > NOW() - INTERVAL '1 day';
        """)
        definition_updates = cursor.fetchone()[0]

        if definition_updates == 0:
            findings.append("INFO: No definition updates detected in trust_synthesiser output in last 24 hours")
        else:
            findings.append(f"INFO: Found {definition_updates} definition updates in trust_synthesiser output in last 24 hours")

        # 4. Check if mcp_scanner is running and has recent activity
        cursor.execute("""
            SELECT COUNT(*)
            FROM mcp_scanner_log
            WHERE event_type = 'definition_update'
            AND timestamp > NOW() - INTERVAL '1 day';
        """)
        scanner_activity = cursor.fetchone()[0]

        if scanner_activity == 0:
            findings.append("WARNING: No definition update activity from mcp_scanner in last 24 hours")
        else:
            findings.append(f"INFO: Found {scanner_activity} definition update activities from mcp_scanner in last 24 hours")

        # 5. Check if there are any records in mcp_definition_history
        cursor.execute("SELECT COUNT(*) FROM mcp_definition_history;")
        history_count = cursor.fetchone()[0]

        if history_count == 0:
            findings.append("FAIL: mcp_definition_history table is empty")
            status = "FAIL"
        else:
            findings.append(f"PASS: mcp_definition_history contains {history_count} records")

        # Determine likely cause if there's a gap
        if status == "FAIL":
            if new_definitions > 0 and definition_updates > 0 and scanner_activity > 0:
                findings.append("LIKELY CAUSE: (b) Pipeline not routing data to mcp_definition_history")
            elif new_definitions > 0 and definition_updates > 0:
                findings.append("LIKELY CAUSE: (a) mcp_scanner not emitting definition_history records")
            elif new_definitions > 0:
                findings.append("LIKELY CAUSE: (b) Pipeline not routing between trust_synthesiser and mcp_scanner")
            else:
                findings.append("LIKELY CAUSE: (a) No new definitions being detected in signal_analyser")

    except Exception as e:
        findings.append(f"ERROR: {str(e)}")
        status = "FAIL"
    finally:
        if 'conn' in locals():
            conn.close()

    # Print results
    print(f"INVESTIGATION STATUS: {status}")
    print("\nFINDINGS:")
    for finding in findings:
        print(f"- {finding}")

if __name__ == "__main__":
    investigate_definition_history_gap()