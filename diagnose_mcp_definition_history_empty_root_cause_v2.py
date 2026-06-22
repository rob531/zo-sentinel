#!/usr/bin/env python3
"""
Root-cause diagnostic for why mcp_definition_history is empty (0 rows).

Determines whether the table is empty because:
(a) the scanner never writes to it
(b) the table schema is wrong
(c) the insert logic is guarded by a feature flag
(d) a foreign-key or trigger silently fails

Returns a concrete fix directive.
"""

import ast
import json
import os
import sqlite3
import subprocess
import sys
from typing import Any


# Constants
TABLE_NAME = 'mcp_definition_history'
SCANNER_FILE = 'mcp_scanner.py'


def diagnose_definition_history_empty() -> dict:
    """
    Diagnose why mcp_definition_history table is empty.
    
    Returns:
        dict with keys:
        - table_row_count: int
        - table_schema: [{name, data_type, is_nullable}]
        - insert_attempts_logged: bool
        - foreign_key_chain: [list of referenced tables and their counts]
        - hypothesis: one of 'never_written'|'schema_mismatch'|'feature_gated'|'fk_silent_fail'
        - recommendation: specific next directive task name and description
    """
    result = {
        'table_row_count': 0,
        'table_schema': [],
        'insert_attempts_logged': False,
        'foreign_key_chain': [],
        'hypothesis': 'unknown',
        'recommendation': ''
    }
    
    # Get row count
    row_count = get_table_row_count()
    result['table_row_count'] = row_count
    
    # Self-test: if table has rows, hypothesis is 'table_populated'
    if row_count > 0:
        result['hypothesis'] = 'table_populated'
        result['recommendation'] = 'No action required - table has data'
        print("\n=== DIAGNOSTIC RESULTS ===")
        print(json.dumps(result, indent=2, default=str))
        return result
    
    # Get table schema
    result['table_schema'] = get_table_schema()
    
    # Check if insert attempts are logged (static source analysis)
    insert_found, is_feature_gated = analyze_scanner_for_inserts()
    result['insert_attempts_logged'] = insert_found
    
    # Get foreign key chain and counts
    result['foreign_key_chain'] = get_foreign_key_chain()
    
    # Determine hypothesis
    if insert_found:
        if is_feature_gated:
            result['hypothesis'] = 'feature_gated'
            result['recommendation'] = 'TASK: disable_feature_flag_mcp_definition_history - Remove or set to True the feature flag guarding insert into mcp_definition_history in mcp_scanner.py'
        else:
            # Insert exists but table is empty - likely FK issue or schema mismatch
            if result['foreign_key_chain']:
                result['hypothesis'] = 'fk_silent_fail'
                ref_tables = [fk['table'] for fk in result['foreign_key_chain']]
                result['recommendation'] = f'TASK: fix_fk_constraints_mcp_definition_history - Foreign key silent fail detected. Referenced tables {ref_tables} may have zero rows or constraint violations. Ensure referenced tables have data.'
            else:
                result['hypothesis'] = 'schema_mismatch'
                result['recommendation'] = 'TASK: verify_insert_syntax_mcp_definition_history - INSERT statement found but may have schema mismatch. Verify column names and types match table schema.'
    else:
        result['hypothesis'] = 'never_written'
        result['recommendation'] = 'TASK: implement_insert_mcp_definition_history - Add INSERT statements into mcp_definition_history in mcp_scanner.py'
    
    # Print findings
    print("\n=== DIAGNOSTIC RESULTS ===")
    print(json.dumps(result, indent=2, default=str))
    
    return result


def get_table_row_count() -> int:
    """Get row count from mcp_definition_history table."""
    db_path = os.environ.get('MCP_DB_PATH', 'mcp.db')
    
    if not os.path.exists(db_path):
        # Try to find database
        for candidate in ['mcp.db', 'mcp_database.db', 'database/mcp.db']:
            if os.path.exists(candidate):
                db_path = candidate
                break
        else:
            return 0
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except sqlite3.OperationalError:
        return 0
    except Exception:
        return 0


def get_table_schema() -> list:
    """Get schema of mcp_definition_history table."""
    db_path = os.environ.get('MCP_DB_PATH', 'mcp.db')
    
    if not os.path.exists(db_path):
        for candidate in ['mcp.db', 'mcp_database.db', 'database/mcp.db']:
            if os.path.exists(candidate):
                db_path = candidate
                break
        else:
            return []
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try information_schema first (SQLite doesn't have this, but some DBs do)
        try:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = ?",
                (TABLE_NAME,)
            )
            columns = cursor.fetchall()
            if columns:
                conn.close()
                return [
                    {'name': col[0], 'data_type': col[1], 'is_nullable': col[2] == 'YES'}
                    for col in columns
                ]
        except sqlite3.OperationalError:
            pass
        
        # Fall back to PRAGMA for SQLite
        cursor.execute(f"PRAGMA table_info({TABLE_NAME})")
        columns = cursor.fetchall()
        conn.close()
        
        if not columns:
            return []
        
        return [
            {'name': col[1], 'data_type': col[2], 'is_nullable': not col[3]}
            for col in columns
        ]
    except Exception:
        return []


def analyze_scanner_for_inserts():
    """
    Static source analysis via AST to check if mcp_scanner.py calls INSERT on mcp_definition_history.
    
    Returns:
        tuple: (insert_found: bool, is_feature_gated: bool)
    """
    if not os.path.exists(SCANNER_FILE):
        # Try common paths
        for candidate in ['mcp_scanner.py', './scanner/mcp_scanner.py', 'src/mcp_scanner.py']:
            if os.path.exists(candidate):
                with open(candidate, 'r') as f:
                    source = f.read()
                break
        else:
            return False, False
    else:
        with open(SCANNER_FILE, 'r') as f:
            source = f.read()
    
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, False
    
    insert_found = False
    is_feature_gated = False
    
    class InsertVisitor(ast.NodeVisitor):
        def __init__(self):
            self.insert_nodes = []
            self.current_condition = None
            
        def visit_If(self, node):
            old_condition = self.current_condition
            self.current_condition = node.test
            self.generic_visit(node)
            self.current_condition = old_condition
            
        def visit_Call(self, node):
            # Check for SQL insert patterns
            if isinstance(node.func, ast.Attribute):
                method_name = node.func.attr.lower()
            elif isinstance(node.func, ast.Name):
                method_name = node.func.id.lower()
            else:
                self.generic_visit(node)
                return
            
            if method_name in ('execute', 'executemany', 'insert', 'run'):
                # Check if table name appears in arguments
                for arg in ast.walk(node):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if TABLE_NAME in arg.value.lower():
                            self.insert_nodes.append({
                                'node': node,
                                'condition': self.current_condition
                            })
            self.generic_visit(node)
    
    visitor = InsertVisitor()
    visitor.visit(tree)
    
    if visitor.insert_nodes:
        insert_found = True
        # Check if any insert is feature-gated
        for insert_info in visitor.insert_nodes:
            condition = insert_info.get('condition')
            if condition is not None:
                # Check if condition mentions feature flags
                cond_str = ast.unparse(condition) if hasattr(ast, 'unparse') else ''
                if any(flag in cond_str.lower() for flag in 
                       ['enable', 'disable', 'flag', 'feature', 'active', 'use']):
                    is_feature_gated = True
                    break
                # Check condition structure for feature gating patterns
                if isinstance(condition, (ast.Name, ast.Attribute)):
                    is_feature_gated = True
                    break
                if isinstance(condition, ast.UnaryOp) and isinstance(condition.op, ast.Not):
                    is_feature_gated = True
                    break
    
    return insert_found, is_feature_gated


def get_foreign_key_chain() -> list:
    """Get foreign key references from mcp_definition_history table."""
    db_path = os.environ.get('MCP_DB_PATH', 'mcp.db')
    
    if not os.path.exists(db_path):
        for candidate in ['mcp.db', 'mcp_database.db', 'database/mcp.db']:
            if os.path.exists(candidate):
                db_path = candidate
                break
        else:
            return []
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try PRAGMA for SQLite
        try:
            cursor.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")
            fks = cursor.fetchall()
            
            if fks:
                result = []
                for fk in fks:
                    # fk format: (id, seq, table, from, to, on_update, on_delete, match)
                    ref_table = fk[2]
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {ref_table}")
                        ref_count = cursor.fetchone()[0]
                    except Exception:
                        ref_count = -1
                    result.append({
                        'table': ref_table,
                        'row_count': ref_count
                    })
                conn.close()
                return result
        except sqlite3.OperationalError:
            pass
        
        # Try information_schema for other DBs
        try:
            cursor.execute(
                "SELECT kcu.table_name, kcu.column_name "
                "FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "ON rc.constraint_name = kcu.constraint_name "
                "WHERE rc.table_name = ?",
                (TABLE_NAME,)
            )
            refs = cursor.fetchall()
            
            result = []
            for ref_table, ref_col in refs:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {ref_table}")
                    ref_count = cursor.fetchone()[0]
                except Exception:
                    ref_count = -1
                result.append({
                    'table': ref_table,
                    'row_count': ref_count
                })
            conn.close()
            return result
        except Exception:
            pass
        
        conn.close()
        return []
    except Exception:
        return []


def main():
    """Main entry point."""
    result = diagnose_definition_history_empty()
    
    # Exit codes
    if result['hypothesis'] == 'table_populated':
        print("\n[RESULT] PASS - Table has data")
        sys.exit(0)
    elif result['hypothesis'] in ('never_written', 'schema_mismatch', 'feature_gated', 'fk_silent_fail'):
        print(f"\n[RESULT] PASS - Hypothesis confirmed: {result['hypothesis']}")
        print(f"[RECOMMENDATION] {result['recommendation']}")
        sys.exit(0)
    else:
        print("\n[RESULT] FAIL - Diagnosis inconclusive")
        sys.exit(1)


if __name__ == '__main__':
    main()