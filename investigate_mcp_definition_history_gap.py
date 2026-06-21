#!/usr/bin/env python3
import logging
import subprocess
import psycopg2
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('investigate_mcp_definition_history_gap.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def check_upstream_data_sources():
    """Check upstream data sources for MCP definitions."""
    logger.info("Checking upstream data sources...")
    try:
        # Example: Check if the source API is accessible
        result = subprocess.run(['curl', '-I', 'https://api.example.com/mcp-definitions'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logger.error(f"Upstream API check failed: {result.stderr}")
            return False
        logger.info("Upstream data source is accessible.")
        return True
    except Exception as e:
        logger.error(f"Error checking upstream data sources: {e}")
        return False

def check_mcp_scanner_daemon():
    """Check the mcp_scanner daemon for errors or misconfigurations."""
    logger.info("Checking mcp_scanner daemon...")
    try:
        # Example: Check if the daemon is running
        result = subprocess.run(['systemctl', 'is-active', 'mcp_scanner'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or result.stdout.strip() != 'active':
            logger.error("mcp_scanner daemon is not running.")
            return False

        # Example: Check logs for errors
        log_result = subprocess.run(['journalctl', '-u', 'mcp_scanner', '--no-pager', '-n', '50'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if 'ERROR' in log_result.stdout or 'CRITICAL' in log_result.stdout:
            logger.error("Errors found in mcp_scanner logs.")
            return False
        logger.info("mcp_scanner daemon is running without errors.")
        return True
    except Exception as e:
        logger.error(f"Error checking mcp_scanner daemon: {e}")
        return False

def check_mcp_data_seeder_daemon():
    """Check the mcp_data_seeder daemon for errors or misconfigurations."""
    logger.info("Checking mcp_data_seeder daemon...")
    try:
        # Example: Check if the daemon is running
        result = subprocess.run(['systemctl', 'is-active', 'mcp_data_seeder'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or result.stdout.strip() != 'active':
            logger.error("mcp_data_seeder daemon is not running.")
            return False

        # Example: Check logs for errors
        log_result = subprocess.run(['journalctl', '-u', 'mcp_data_seeder', '--no-pager', '-n', '50'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if 'ERROR' in log_result.stdout or 'CRITICAL' in log_result.stdout:
            logger.error("Errors found in mcp_data_seeder logs.")
            return False
        logger.info("mcp_data_seeder daemon is running without errors.")
        return True
    except Exception as e:
        logger.error(f"Error checking mcp_data_seeder daemon: {e}")
        return False

def check_mcp_definition_history_table():
    """Check the mcp_definition_history table for data gaps."""
    logger.info("Checking mcp_definition_history table...")
    try:
        conn = psycopg2.connect(
            dbname='zo_sentinel',
            user='postgres',
            password='password',
            host='localhost'
        )
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM mcp_definition_history")
        count = cursor.fetchone()[0]
        logger.info(f"Found {count} records in mcp_definition_history table.")

        if count == 0:
            logger.warning("mcp_definition_history table is empty.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking mcp_definition_history table: {e}")
        return False
    finally:
        if conn:
            conn.close()

def propose_solution():
    """Propose a solution if a data gap is identified."""
    logger.info("Proposing solution for data gap...")
    solution = """
    1. Verify the configuration of mcp_scanner and mcp_data_seeder daemons.
    2. Ensure that the upstream data source is correctly configured and accessible.
    3. Manually trigger the mcp_scanner and mcp_data_seeder daemons to reprocess data.
    4. If the issue persists, consider implementing a data recovery script to backfill missing data.
    """
    logger.info(f"Proposed solution:\n{solution}")
    return solution

def main():
    logger.info("Starting investigation of mcp_definition_history gap...")

    upstream_ok = check_upstream_data_sources()
    scanner_ok = check_mcp_scanner_daemon()
    seeder_ok = check_mcp_data_seeder_daemon()
    table_ok = check_mcp_definition_history_table()

    if not upstream_ok or not scanner_ok or not seeder_ok or not table_ok:
        logger.warning("Data gap identified in mcp_definition_history table.")
        solution = propose_solution()
        logger.info("Follow-up directive: Implement the proposed solution and monitor the system.")
    else:
        logger.info("No data gap identified in mcp_definition_history table.")

if __name__ == "__main__":
    main()