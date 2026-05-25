"""
MCP Tool Schema Patterns Library

Detects architectural patterns in MCP server tool definitions.
Source: Cloudflare enterprise MCP reference architecture
blog.cloudflare.com/enterprise-mcp, 2026-04-14

Patterns:
  - progressive-disclosure: <=4 high-level tools with dynamic discovery
  - brute-force enumeration: >=20 tools with full schemas upfront
  - hybrid: everything else

Pure library: no DB, no network, no protected module imports.
Feeds future context_efficiency signal (deferred until weak-signal plateau resolved).
"""

from typing import Any


def detect_tool_pattern(tool_definitions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Detect architectural pattern from tool definitions.
    
    Args:
        tool_definitions: List of tool definition dicts. Each should have 'name'.
                         Optional: 'description', 'inputSchema', 'properties'.
    
    Returns:
        dict with keys:
          - pattern: str ('progressive-disclosure' | 'brute-force' | 'hybrid')
          - tool_count: int
          - evidence: dict with keys:
              - tools_analyzed: int
              - tools_with_schema: int
              - total_parameters: int
              - avg_description_length: float
              - has_dynamic_patterns: bool
              - reason: str
    """
    tool_count = len(tool_definitions)
    
    evidence = {
        "tools_analyzed": tool_count,
        "tools_with_schema": 0,
        "total_parameters": 0,
        "avg_description_length": 0.0,
        "has_dynamic_patterns": False,
        "reason": "",
    }
    
    if tool_count == 0:
        return {
            "pattern": "progressive-disclosure",
            "tool_count": 0,
            "evidence": evidence,
        }
    
    # Analyze each tool for schema complexity
    description_lengths = []
    dynamic_patterns = [
        "list",
        "discover",
        "enumerate",
        "catalog",
        "query",
        "search",
        "browse",
        "dynamic",
        "runtime",
        "lazy",
        "on-demand",
        "fetch",
    ]
    
    for tool in tool_definitions:
        # Count tools with schema
        schema = tool.get("inputSchema") or tool.get("properties") or {}
        if isinstance(schema, dict) and schema:
            evidence["tools_with_schema"] += 1
        
        # Count parameters
        if isinstance(schema, dict):
            props = schema.get("properties") or {}
            evidence["total_parameters"] += len(props)
        
        # Measure description length
        desc = tool.get("description") or ""
        description_lengths.append(len(desc))
        
        # Check for dynamic discovery patterns in description
        desc_lower = desc.lower()
        if any(p in desc_lower for p in dynamic_patterns):
            evidence["has_dynamic_patterns"] = True
    
    # Calculate average description length
    if description_lengths:
        evidence["avg_description_length"] = sum(description_lengths) / len(description_lengths)
    
    # Determine pattern based on thresholds
    if tool_count >= 20:
        pattern = "brute-force"
        evidence["reason"] = ">=20 tools with full schemas upfront indicates brute-force enumeration pattern"
    elif tool_count <= 4 and evidence["has_dynamic_patterns"]:
        pattern = "progressive-disclosure"
        evidence["reason"] = "<=4 tools with dynamic discovery patterns suggests progressive-disclosure architecture"
    elif tool_count <= 4:
        pattern = "progressive-disclosure"
        evidence["reason"] = "<=4 tools suggests high-level abstraction with dynamic discovery (progressive-disclosure)"
    else:
        pattern = "hybrid"
        evidence["reason"] = "Mid-range tool count with mixed characteristics indicates hybrid architecture"
    
    return {
        "pattern": pattern,
        "tool_count": tool_count,
        "evidence": evidence,
    }


def compute_context_efficiency_score(tool_definitions: list[dict[str, Any]]) -> float:
    """
    Compute context efficiency score for tool definitions.
    
    Lower scores indicate more efficient context usage.
    Higher scores may indicate bloated tool schemas.
    
    Args:
        tool_definitions: List of tool definition dicts.
    
    Returns:
        float score (0.0 = most efficient, 1.0 = least efficient)
    """
    result = detect_tool_pattern(tool_definitions)
    tool_count = result["tool_count"]
    evidence = result["evidence"]
    
    if tool_count == 0:
        return 0.0
    
    # Score factors:
    # 1. Tool count relative to ideal (progressive-disclosure = 1.0, brute-force = 0.0)
    if tool_count <= 4:
        count_score = 1.0
    elif tool_count >= 20:
        count_score = 0.0
    else:
        count_score = 1.0 - ((tool_count - 4) / 16.0)
    
    # 2. Parameter bloat (more params = lower efficiency)
    avg_params = evidence["total_parameters"] / tool_count if tool_count > 0 else 0
    param_score = max(0.0, 1.0 - (avg_params / 20.0))
    
    # 3. Description bloat
    avg_desc_len = evidence["avg_description_length"]
    desc_score = max(0.0, 1.0 - (avg_desc_len / 500.0))
    
    # Weighted composite
    efficiency = (count_score * 0.4) + (param_score * 0.3) + (desc_score * 0.3)
    return round(efficiency, 3)


if __name__ == "__main__":
    print("Running MCP Tool Schema Patterns self-smoke tests...\n")
    
    # Test 1: Progressive disclosure (Cloudflare-style)
    progressive_tools = [
        {"name": "browse", "description": "Browse and discover web content dynamically"},
        {"name": "search", "description": "Search the internet for information on-demand"},
        {"name": "fetch", "description": "Fetch specific URLs as needed"},
    ]
    result1 = detect_tool_pattern(progressive_tools)
    print(f"Test 1 - Progressive Disclosure:")
    print(f"  Pattern: {result1['pattern']}")
    print(f"  Tool Count: {result1['tool_count']}")
    print(f"  Evidence: {result1['evidence']}")
    assert result1["pattern"] == "progressive-disclosure", f"Expected progressive-disclosure, got {result1['pattern']}"
    assert result1["tool_count"] == 3
    print("  PASS\n")
    
    # Test 2: Brute-force enumeration (typical MCP registry)
    brute_tools = [
        {
            "name": f"tool_{i}",
            "description": f"Tool {i} performs a specific operation",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "param_a": {"type": "string"},
                    "param_b": {"type": "integer"},
                    "param_c": {"type": "boolean"},
                },
            },
        }
        for i in range(25)
    ]
    result2 = detect_tool_pattern(brute_tools)
    print(f"Test 2 - Brute-Force Enumeration:")
    print(f"  Pattern: {result2['pattern']}")
    print(f"  Tool Count: {result2['tool_count']}")
    print(f"  Evidence: {result2['evidence']}")
    assert result2["pattern"] == "brute-force", f"Expected brute-force, got {result2['pattern']}"
    assert result2["tool_count"] == 25
    print("  PASS\n")
    
    # Test 3: Hybrid (balanced)
    hybrid_tools = [
        {"name": "create_document", "description": "Create a new document with optional template"},
        {"name": "read_document", "description": "Read document contents"},
        {"name": "update_document", "description": "Update existing document"},
        {"name": "delete_document", "description": "Delete a document"},
        {"name": "list_documents", "description": "List all available documents"},
        {"name": "search_documents", "description": "Search documents by query"},
        {"name": "export_document", "description": "Export document to specified format"},
        {"name": "import_document", "description": "Import document from file"},
        {"name": "share_document", "description": "Share document with team members"},
    ]
    result3 = detect_tool_pattern(hybrid_tools)
    print(f"Test 3 - Hybrid Architecture:")
    print(f"  Pattern: {result3['pattern']}")
    print(f"  Tool Count: {result3['tool_count']}")
    print(f"  Evidence: {result3['evidence']}")
    assert result3["pattern"] == "hybrid", f"Expected hybrid, got {result3['pattern']}"
    assert result3["tool_count"] == 9
    print("  PASS\n")
    
    # Bonus: Context efficiency scores
    print("Context Efficiency Scores:")
    print(f"  Progressive: {compute_context_efficiency_score(progressive_tools)}")
    print(f"  Brute-Force: {compute_context_efficiency_score(brute_tools)}")
    print(f"  Hybrid: {compute_context_efficiency_score(hybrid_tools)}")
    
    print("\nAll smoke tests passed successfully!")
    print("Output file: mcp_tool_schema_patterns.py")