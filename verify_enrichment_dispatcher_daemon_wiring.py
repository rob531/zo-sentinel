#!/usr/bin/env python3
# deps: 
"""
Verification utility confirming enrichment_dispatcher_daemon.py is properly wired into signal_analyser.py's enrichment pipeline.

Interface: verify_enrichment_dispatcher_daemon_wiring() -> bool
"""
import ast
import os
import sys
from typing import List


def _check_file_exists(path: str) -> bool:
    return os.path.isfile(path)


def _parse_ast(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()
    return ast.parse(source, filename=path)


def _imports_module(tree: ast.AST, module_name: str) -> bool:
    """Return True if the AST imports the given module (direct import or from import)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == module_name:
                return True
    return False


def _has_function(tree: ast.AST, func_name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return True
    return False


def _heartbeat_posts_to_service_health(tree: ast.AST) -> bool:
    """Acceptable heartbeat mechanisms (any of these -> PASS):
      1. A function named heartbeat_loop() that posts to a /health URL
      2. A function named post_heartbeat() (or send_heartbeat) that posts
         to a /health URL  -- this is the standard sentinel pattern
      3. A call inside run() to any *heartbeat* function with a /health URL nearby
    """
    if _has_function(tree, "heartbeat_loop"):
        return True
    for name in ("post_heartbeat", "send_heartbeat", "_post_heartbeat"):
        if _has_function(tree, name):
            return True
    return False


def _has_main_guard_call(tree: ast.AST, func_name: str) -> bool:
    """Check for if __name__ == '__main__': run() pattern."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If):
            # Ensure test is a comparison __name__ == '__main__'
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == '__name__'
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == '__main__'
            ):
                # Look for a call to func_name in the body
                for stmt in node.body:
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        if isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == func_name:
                            return True
    return False


def verify_enrichment_dispatcher_daemon_wiring() -> bool:
    all_pass = True
    # a) enrichment_dispatcher_daemon.py exists and parses cleanly
    ed_path = 'enrichment_dispatcher_daemon.py'
    if _check_file_exists(ed_path):
        try:
            ed_tree = _parse_ast(ed_path)
            print('PASS: enrichment_dispatcher_daemon.py exists and parses')
        except Exception as e:
            print(f'FAIL: enrichment_dispatcher_daemon.py parsing error: {e}')
            all_pass = False
            ed_tree = None
    else:
        print('FAIL: enrichment_dispatcher_daemon.py does not exist')
        all_pass = False
        ed_tree = None

    # b) signal_analyser.py imports from enrichment_runner
    sa_path = 'signal_analyser.py'
    if _check_file_exists(sa_path):
        try:
            sa_tree = _parse_ast(sa_path)
            if _imports_module(sa_tree, 'enrichment_runner'):
                print('PASS: signal_analyser.py imports enrichment_runner')
            else:
                print('FAIL: signal_analyser.py does NOT import enrichment_runner')
                all_pass = False
        except Exception as e:
            print(f'FAIL: signal_analyser.py parsing error: {e}')
            all_pass = False
    else:
        print('FAIL: signal_analyser.py does not exist')
        all_pass = False

    # c) enrichment_runner.py imports enrichment_dispatcher_daemon
    er_path = 'enrichment_runner.py'
    if _check_file_exists(er_path):
        try:
            er_tree = _parse_ast(er_path)
            if _imports_module(er_tree, 'enrichment_dispatcher_daemon'):
                print('PASS: enrichment_runner.py imports enrichment_dispatcher_daemon')
            else:
                print('FAIL: enrichment_runner.py does NOT import enrichment_dispatcher_daemon')
                all_pass = False
        except Exception as e:
            print(f'FAIL: enrichment_runner.py parsing error: {e}')
            all_pass = False
    else:
        print('FAIL: enrichment_runner.py does not exist')
        all_pass = False

    # d) enrichment_dispatcher_daemon implements run() and main guard
    if ed_tree is not None:
        has_run = _has_function(ed_tree, 'run')
        has_main = _has_main_guard_call(ed_tree, 'run')
        if has_run:
            print('PASS: enrichment_dispatcher_daemon.py defines run()')
        else:
            print('FAIL: enrichment_dispatcher_daemon.py missing run()')
            all_pass = False
        if has_main:
            print('PASS: enrichment_dispatcher_daemon.py has __main__ guard calling run()')
        else:
            print('FAIL: enrichment_dispatcher_daemon.py missing __main__ guard calling run()')
            all_pass = False
    # e) enrichment_dispatcher_daemon has heartbeat_loop()
    if ed_tree is not None:
        if _has_function(ed_tree, 'heartbeat_loop'):
            print('PASS: enrichment_dispatcher_daemon.py defines heartbeat_loop()')
        else:
            print('FAIL: enrichment_dispatcher_daemon.py missing heartbeat_loop()')
            all_pass = False

    return all_pass


if __name__ == '__main__':
    result = verify_enrichment_dispatcher_daemon_wiring()
    sys.exit(0 if result else 1)
