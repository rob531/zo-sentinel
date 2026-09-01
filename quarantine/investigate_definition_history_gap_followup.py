#!/usr/bin/env python3
"""
investigate_definition_history_gap_followup.py
Follow-up diagnostic for mcp_definition_history table remaining empty.

Checks:
1. What triggers definition_history inserts
2. Whether mcp_scanner or another daemon populates it
3. Service health status of relevant daemons

Pure diagnostic - NO database writes.
"""

import sys
import os
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.db_config import get_session, DatabaseConfig
from sqlalchemy import text, inspect
from models.models import ServiceHealth, MCPDefinition, MCPDefinitionHistory


def print_header(title: str) -> None:
    """Print formatted section header."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)


def check_table_schema() -> None:
    """Check the schema of mcp_definition_history table."""
    print_header("1. TABLE SCHEMA ANALYSIS")
    
    session = get_session()
    try:
        inspector = inspect(session.get_bind())
        
        # Check mcp_definition_history columns
        columns = inspector.get_columns('mcp_definition_history')
        print("\nmcp_definition_history columns:")
        for col in columns:
            nullable = "NULL" if col['nullable'] else "NOT NULL"
            default = f"DEFAULT {col['default']}" if col['default'] else ""
            print(f"  - {col['name']}: {col['type']} {nullable} {default}")
        
        # Check for indexes
        indexes = inspector.get_indexes('mcp_definition_history')
        if indexes:
            print("\nIndexes:")
            for idx in indexes:
                unique = "UNIQUE" if idx['unique'] else ""
                print(f"  - {idx['name']}: {idx['column_names']} {unique}")
        
        # Check for foreign keys
        fks = inspector.get_foreign_keys('mcp_definition_history')
        if fks:
            print("\nForeign Keys:")
            for fk in fks:
                print(f"  - {fk['name']}: {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")
        
    except Exception as e:
        print(f"  ERROR checking schema: {e}")
    finally:
        session.close()


def check_trigger_mechanisms() -> None:
    """Investigate what mechanisms insert into definition_history."""
    print_header("2. INSERT TRIGGER MECHANISMS")
    
    session = get_session()
    try:
        # Check for INSERT triggers on the table
        inspector = inspect(session.get_bind())
        
        # Query information_schema for triggers
        query = text("""
            SELECT 
                trigger_name,
                event_manipulation,
                action_statement,
                created
            FROM information_schema.triggers 
            WHERE event_object_schema = :schema
            AND event_object_table = 'mcp_definition_history'
            AND event_manipulation = 'INSERT'
        """)
        
        result = session.execute(query, {"schema": DatabaseConfig.SCHEMA})
        triggers = result.fetchall()
        
        if triggers:
            print("\nFound INSERT triggers on mcp_definition_history:")
            for trigger in triggers:
                print(f"  - Trigger: {trigger[0]}")
                print(f"    Event: {trigger[1]}")
                print(f"    Action: {trigger[2][:200]}...")
                print()
        else:
            print("\n  NO INSERT triggers found on mcp_definition_history table")
        
        # Check if there are any rules (PostgreSQL)
        try:
            rule_query = text("""
                SELECT rulename, definition
                FROM pg_rules
                WHERE tablename = 'mcp_definition_history'
                AND evtaction LIKE '%INSERT%'
            """)
            result = session.execute(rule_query)
            rules = result.fetchall()
            
            if rules:
                print("\nFound INSERT rules:")
                for rule in rules:
                    print(f"  - Rule: {rule[0]}")
            else:
                print("\n  NO INSERT rules found")
        except Exception:
            pass  # Not PostgreSQL or other issue
        
    except Exception as e:
        print(f"  ERROR checking triggers: {e}")
    finally:
        session.close()


def check_source_tables() -> None:
    """Check source tables that might feed definition_history."""
    print_header("3. SOURCE TABLE RELATIONSHIPS")
    
    session = get_session()
    try:
        inspector = inspect(session.get_bind())
        
        # Check mcp_definition table
        print("\nChecking mcp_definition table (potential source):")
        def_columns = inspector.get_columns('mcp_definition')
        for col in def_columns:
            print(f"  - {col['name']}: {col['type']}")
        
        # Check row counts
        print("\nRow counts:")
        try:
            result = session.execute(text("SELECT COUNT(*) FROM mcp_definition"))
            count = result.scalar()
            print(f"  - mcp_definition: {count} rows")
        except Exception as e:
            print(f"  - mcp_definition: ERROR - {e}")
        
        try:
            result = session.execute(text("SELECT COUNT(*) FROM mcp_definition_history"))
            count = result.scalar()
            print(f"  - mcp_definition_history: {count} rows")
        except Exception as e:
            print(f"  - mcp_definition_history: ERROR - {e}")
        
        # Check for recent inserts in mcp_definition
        print("\nRecent mcp_definition activity:")
        try:
            result = session.execute(text("""
                SELECT created_at, updated_at 
                FROM mcp_definition 
                ORDER BY updated_at DESC 
                LIMIT 5
            """))
            rows = result.fetchall()
            if rows:
                for row in rows:
                    print(f"  - created_at: {row[0]}, updated_at: {row[1]}")
            else:
                print("  No rows in mcp_definition")
        except Exception as e:
            print(f"  ERROR: {e}")
            
    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        session.close()


def check_service_health() -> None:
    """Check service health for relevant daemons."""
    print_header("4. SERVICE HEALTH STATUS")
    
    session = get_session()
    try:
        # Get all service health entries
        result = session.execute(text("""
            SELECT service_name, status, last_check, message
            FROM service_health
            ORDER BY service_name
        """))
        services = result.fetchall()
        
        if services:
            print(f"\n{'Service Name':<30} {'Status':<12} {'Last Check':<25} Message")
            print("-" * 100)
            for service in services:
                service_name, status, last_check, message = service
                last_check_str = str(last_check)[:24] if last_check else "N/A"
                message_str = str(message)[:40] if message else ""
                print(f"{service_name:<30} {status:<12} {last_check_str:<25} {message_str}")
        else:
            print("\n  NO service health records found")
        
        # Check specifically for MCP-related services
        print("\n\nMCP-related services:")
        mcp_services = [s for s in services if 'mcp' in str(s[0]).lower() or 'scanner' in str(s[0]).lower()]
        if mcp_services:
            for service in mcp_services:
                print(f"  - {service[0]}: {service[1]}")
        else:
            print("  No MCP-specific services found in service_health table")
        
    except Exception as e:
        print(f"  ERROR checking service health: {e}")
    finally:
        session.close()


def check_daemon_code() -> None:
    """Search for code that inserts into definition_history."""
    print_header("5. CODE ANALYSIS - INSERT SOURCES")
    
    # Look for files that might contain insert logic
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    search_patterns = [
        'definition_history.insert',
        'DefinitionHistory',
        'mcp_scanner',
        'insert_history',
    ]
    
    files_to_check = [
        'mcp_scanner.py',
        'daemon.py', 
        'services/mcp_service.py',
        'services/definition_service.py',
        'background_tasks.py',
        'processors/definition_processor.py',
    ]
    
    print("\nSearching for definition_history insert references:")
    
    for file_path in files_to_check:
        full_path = os.path.join(project_root, file_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r') as f:
                    content = f.read()
                    for pattern in search_patterns:
                        if pattern.lower() in content.lower():
                            print(f"\n  Found '{pattern}' in {file_path}")
                            # Show context
                            lines = content.split('\n')
                            for i, line in enumerate(lines):
                                if pattern.lower() in line.lower():
                                    context_start = max(0, i-2)
                                    context_end = min(len(lines), i+3)
                                    print(f"    Line {i+1}: {line.strip()}")
            except Exception as e:
                print(f"  ERROR reading {file_path}: {e}")
        else:
            print(f"  File not found: {file_path}")
    
    # Check models
    print("\n\nDefinitionHistory model inspection:")
    try:
        print(f"  Model class: {MCPDefinitionHistory}")
        print(f"  __tablename__: {MCPDefinitionHistory.__tablename__}")
        print(f"  Table args: {getattr(MCPDefinitionHistory, '__table_args__', None)}")
    except Exception as e:
        print(f"  ERROR: {e}")


def check_trigger_function_code() -> None:
    """Check for trigger functions in the database."""
    print_header("6. DATABASE TRIGGER FUNCTIONS")
    
    session = get_session()
    try:
        # PostgreSQL trigger functions
        query = text("""
            SELECT 
                p.proname AS function_name,
                pg_get_functiondef(p.oid) AS definition
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = :schema
            AND p.proname LIKE '%definition%history%'
        """)
        
        result = session.execute(query, {"schema": DatabaseConfig.SCHEMA})
        funcs = result.fetchall()
        
        if funcs:
            print("\nFound trigger functions related to definition_history:")
            for func in funcs:
                print(f"\n  Function: {func[0]}")
                print(f"  Definition: {func[1][:500]}...")
        else:
            print("\n  NO trigger functions found for definition_history")
            
    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        session.close()


def check_event_log() -> None:
    """Check for any event logs related to definition changes."""
    print_header("7. EVENT LOG / AUDIT TRAIL")
    
    session = get_session()
    try:
        # Check for any audit tables
        inspector = inspect(session.get_bind())
        tables = inspector.get_table_names()
        
        audit_related = [t for t in tables if 'audit' in t.lower() or 'event' in t.lower() or 'log' in t.lower()]
        
        if audit_related:
            print(f"\nFound audit/log tables: {audit_related}")
        else:
            print("\nNo obvious audit/log tables found")
        
        # Check if there's a recent activity log
        if 'event_log' in tables:
            result = session.execute(text("""
                SELECT * FROM event_log 
                ORDER BY created_at DESC 
                LIMIT 10
            """))
            events = result.fetchall()
            print(f"\nevent_log entries: {len(events)}")
            
    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        session.close()


def check_configuration() -> None:
    """Check configuration for history tracking settings."""
    print_header("8. CONFIGURATION ANALYSIS")
    
    print("\nChecking for history tracking configuration:")
    
    # Check for config files
    project_root = os.path.dirname(os.path.abspath(__file__))
    config_files = [
        os.path.join(project_root, 'config.py'),
        os.path.join(project_root, 'settings.py'),
        os.path.join(project_root, '.env'),
    ]
    
    for config_file in config_files:
        if os.path.exists(config_file):
            print(f"\n  Checking {config_file}:")
            try:
                with open(config_file, 'r') as f:
                    content = f.read()
                    for line in content.split('\n'):
                        if 'history' in line.lower() or 'track' in line.lower():
                            print(f"    {line.strip()}")
            except Exception as e:
                print(f"    ERROR reading: {e}")


def main():
    """Run all diagnostic checks."""
    print("=" * 60)
    print(" INVESTIGATION: mcp_definition_history GAP FOLLOW-UP")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Database: {DatabaseConfig.HOST}/{DatabaseConfig.DATABASE}")
    
    check_table_schema()
    check_trigger_mechanisms()
    check_source_tables()
    check_service_health()
    check_daemon_code()
    check_trigger_function_code()
    check_event_log()
    check_configuration()
    
    print_header("DIAGNOSTIC SUMMARY")
    print("""
Key Findings to Investigate:
1. If NO triggers/rules found: History may require explicit application code inserts
2. If mcp_scanner NOT in service_health: Scanner daemon may not be running
3. If mcp_definition HAS rows but history is EMPTY: No INSERT triggers on source
4. Check application logs for any errors during definition updates

Next Steps:
- Verify mcp_scanner daemon is running (check service_health)
- Review application code for explicit history.insert() calls
- Check if history tracking is disabled in config
- Verify database permissions allow INSERT on mcp_definition_history
    """)


if __name__ == "__main__":
    main()