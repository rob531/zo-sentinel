#!/usr/bin/env python3
"""
run_schema.py -- ZO-SENTINEL Schema runner.
Creates and verifies DuckDB schema on startup or call create_all() from any service.
"""

import requests, logging
from schema import TABLES, EXECUTE_URL
import os
import sys

def create_tables():
    log.info("Creating tables...")
    for table in TABLES:
        response = requests.post(EXECUTE_URL, json={'table': table})
        if response.status_code != 200:
            log.error(f"Failed to create table: {response.text}")
            sys.exit(1)
    log.info("Tables created successfully.")

def verify_tables():
    log.info("Verifying tables...")
    ws_query = requests.get(EXECUTE_URL + "/information_schema")
    expected_table_names = [table['name'] for table in ws_query.json()['tables']]
    actual_table_names = [table['name'] for table in TABLES]
    mismatch_count = sum(1 for name in expected_table_names if name not in actual_table_names)
    log.info(f"Mismatch count: {mismatch_count}")
    if mismatch_count > 0:
        log.error("Table verification failed.")
        sys.exit(1)
    else:
        log.info("Tables verified successfully.")

def main():
    create_tables()
    verify_tables()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    main()