#!/usr/bin/env python3
import psycopg2
from datetime import datetime, timedelta

def verify_populator_scheduling():
    try:
        # Connect to the database
        conn = psycopg2.connect(
            dbname="your_database_name",
            user="your_username",
            password="your_password",
            host="your_host"
        )
        cursor = conn.cursor()

        # Check service_health for recent heartbeat
        cursor.execute("""
            SELECT last_heartbeat
            FROM service_health
            WHERE service_name = 'mcp_definition_history_populator'
            ORDER BY last_heartbeat DESC
            LIMIT 1
        """)
        heartbeat_result = cursor.fetchone()

        if not heartbeat_result:
            print("FAIL: No heartbeat found for mcp_definition_history_populator")
            return

        last_heartbeat = heartbeat_result[0]
        heartbeat_age = datetime.now() - last_heartbeat
        if heartbeat_age > timedelta(hours=2):
            print(f"FAIL: Heartbeat is stale (last heartbeat: {last_heartbeat})")
            return

        # Check mcp_definition_history for recent entries
        cursor.execute("""
            SELECT COUNT(*)
            FROM mcp_definition_history
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        recent_entries = cursor.fetchone()[0]

        if recent_entries == 0:
            print("FAIL: No new entries in mcp_definition_history in the last 24 hours")
            return

        print("PASS: mcp_definition_history_populator is running and populating data correctly")
        print(f"  - Last heartbeat: {last_heartbeat}")
        print(f"  - Recent entries in last 24 hours: {recent_entries}")

    except Exception as e:
        print(f"FAIL: Error verifying populator scheduling - {str(e)}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    verify_populator_scheduling()