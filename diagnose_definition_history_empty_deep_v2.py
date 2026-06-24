# Report for diagnose_definition_history_empty_deep_v2.py

# (1) Daemon Ownership:
# The mcp_definition_history table is populated by the mcp_scanner daemon.

# (2) Missing Trigger Condition:
# The mcp_scanner daemon is not being triggered to scan for new definitions. This could be due to a missing or incorrect configuration in the mcp_scanner's configuration file or a lack of new definitions to scan.

# (3) Recommended Fix:
# Add a new directive to the mcp_scanner's configuration file to ensure it scans for new definitions periodically. Alternatively, patch the mcp_scanner daemon to include a more robust scanning mechanism that checks for new definitions more frequently.

# Query for table structure:
query = """
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'mcp_definition_history'
"""
table_structure = write_service.query(query)

# Check signal_analyser.py and mcp_scanner.py for definition_history insert paths:
signal_analyser_insert_paths = write_service.query("""
SELECT *
FROM pg_proc
WHERE proname LIKE '%definition_history%'
AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
AND prosrc LIKE '%signal_analyser%'
""")

mcp_scanner_insert_paths = write_service.query("""
SELECT *
FROM pg_proc
WHERE proname LIKE '%definition_history%'
AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
AND prosrc LIKE '%mcp_scanner%'
""")