#!/usr/bin/env python3
"""
ZO-SENTINEL: Standalone smoke test for mcp_traffic_fingerprints module.
Verifies the module is a pure library with correct exports.
Exits 0 on success, non-zero on failure.
"""
import sys
import importlib.util

def verify_module_structure():
    """Verify mcp_traffic_fingerprints has required exports."""
    print("[1] Verifying module structure...")
    spec = importlib.util.spec_from_file_location(
        "mcp_traffic_fingerprints",
        "/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    required_exports = ['detect_mcp_methods', 'is_mcp_traffic', 'extract_session_indicators']
    missing = [e for e in required_exports if not hasattr(module, e)]
    
    if missing:
        print(f"    FAIL: Missing exports: {missing}")
        return False
    
    for exp in required_exports:
        obj = getattr(module, exp)
        if not callable(obj):
            print(f"    FAIL: {exp} is not callable")
            return False
    
    print(f"    PASS: All required exports present and callable")
    return True

def test_detect_mcp_methods():
    """Test detect_mcp_methods with known MCP inputs."""
    print("\n[2] Testing detect_mcp_methods...")
    spec = importlib.util.spec_from_file_location(
        "mcp_traffic_fingerprints",
        "/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    test_cases = [
        {
            "name": "MCP initialize request",
            "input": {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
            "expected_methods": ["initialize"]
        },
        {
            "name": "MCP tools/list request",
            "input": {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            "expected_methods": ["tools/list"]
        },
        {
            "name": "MCP resources/list request",
            "input": {"jsonrpc": "2.0", "method": "resources/list", "params": {}, "id": 3},
            "expected_methods": ["resources/list"]
        },
    ]
    
    pass_count = 0
    for tc in test_cases:
        try:
            result = module.detect_mcp_methods(tc["input"])
            if result is not None:
                print(f"    PASS: {tc['name']} -> {result}")
                pass_count += 1
            else:
                print(f"    FAIL: {tc['name']} returned None")
        except Exception as e:
            print(f"    FAIL: {tc['name']} -> {e}")
    
    print(f"    Result: {pass_count}/{len(test_cases)} passed")
    return pass_count == len(test_cases)

def test_is_mcp_traffic():
    """Test is_mcp_traffic with known-good JSON-RPC inputs."""
    print("\n[3] Testing is_mcp_traffic...")
    spec = importlib.util.spec_from_file_location(
        "mcp_traffic_fingerprints",
        "/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    test_cases = [
        {
            "name": "Valid JSON-RPC 2.0 with MCP method",
            "input": {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
            "expect_true": True
        },
        {
            "name": "Valid JSON-RPC 2.0 tools/list",
            "input": {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            "expect_true": True
        },
        {
            "name": "Valid JSON-RPC 2.0 resources/list",
            "input": {"jsonrpc": "2.0", "method": "resources/list", "params": {}, "id": 3},
            "expect_true": True
        },
    ]
    
    pass_count = 0
    for tc in test_cases:
        try:
            result = module.is_mcp_traffic(tc["input"])
            if result == tc["expect_true"]:
                print(f"    PASS: {tc['name']} -> {result}")
                pass_count += 1
            else:
                print(f"    FAIL: {tc['name']} -> expected {tc['expect_true']}, got {result}")
        except Exception as e:
            print(f"    FAIL: {tc['name']} -> {e}")
    
    print(f"    Result: {pass_count}/{len(test_cases)} passed")
    return pass_count == len(test_cases)

def test_extract_session_indicators():
    """Test extract_session_indicators with valid session data."""
    print("\n[4] Testing extract_session_indicators...")
    spec = importlib.util.spec_from_file_location(
        "mcp_traffic_fingerprints",
        "/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    test_cases = [
        {
            "name": "Session from initialize request",
            "request": {"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05"}, "id": 1},
            "session_id": "sess-abc123"
        },
        {
            "name": "Session from tools/list request",
            "request": {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            "session_id": "sess-def456"
        },
        {
            "name": "Session from resources/list request",
            "request": {"jsonrpc": "2.0", "method": "resources/list", "params": {}, "id": 3},
            "session_id": "sess-ghi789"
        },
    ]
    
    pass_count = 0
    for tc in test_cases:
        try:
            result = module.extract_session_indicators(tc["request"], tc["session_id"])
            if result is not None:
                print(f"    PASS: {tc['name']} -> {type(result).__name__}")
                pass_count += 1
            else:
                print(f"    FAIL: {tc['name']} returned None")
        except Exception as e:
            print(f"    FAIL: {tc['name']} -> {e}")
    
    print(f"    Result: {pass_count}/{len(test_cases)} passed")
    return pass_count == len(test_cases)

def check_no_protected_imports():
    """Verify module doesn't import protected modules."""
    print("\n[5] Checking for protected imports...")
    with open("/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py", "r") as f:
        content = f.read()
    
    protected = [
        "duckdb",
        "requests",
        "psycopg2",
        "sqlite3",
        "pymongo",
        "redis",
        "mysql",
    ]
    
    found = []
    for prot in protected:
        if f"import {prot}" in content or f"from {prot}" in content:
            found.append(prot)
    
    if found:
        print(f"    FAIL: Found protected imports: {found}")
        return False
    
    print("    PASS: No protected imports detected")
    return True

def main():
    """Main verification entry point."""
    print("=" * 60)
    print("ZO-SENTINEL: mcp_traffic_fingerprints Standalone Smoke Test")
    print("=" * 60)
    
    results = []
    results.append(("Module structure", verify_module_structure()))
    results.append(("detect_mcp_methods", test_detect_mcp_methods()))
    results.append(("is_mcp_traffic", test_is_mcp_traffic()))
    results.append(("extract_session_indicators", test_extract_session_indicators()))
    results.append(("Protected imports", check_no_protected_imports()))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    if all_passed:
        print("SUCCESS: All verifications passed (exit 0)")
        return 0
    else:
        print("FAILURE: Some verifications failed (exit 1)")
        return 1

if __name__ == "__main__":
    sys.exit(main())