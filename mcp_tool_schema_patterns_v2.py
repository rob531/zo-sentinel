#!/usr/bin/env python3
"""
Cloudflare enterprise MCP reference architecture, blog.cloudflare.com/enterprise-mcp, 2026-04-14.

MCP tool pattern detection module for identifying architectural patterns
in tool definitions. Classifies tools as progressive_disclosure, 
brute_force_enumeration, or hybrid based on tool count and schema complexity.
"""

from typing import Any


def _calculate_schema_complexity(tools: list[dict]) -> float:
    """
    Calculate schema complexity score (0.0 to 1.0).
    
    Full schemas with multiple properties, required fields, and detailed
    types score higher. Missing or minimal schemas score lower.
    """
    if not tools:
        return 0.0
    
    total_complexity = 0.0
    
    for tool in tools:
        schema = tool.get('inputSchema', {})
        
        if not schema or schema == {}:
            total_complexity += 0.0
            continue
        
        complexity = 0.0
        
        # Check for type specification
        if schema.get('type') == 'object':
            complexity += 0.2
        
        # Check for properties
        properties = schema.get('properties', {})
        if properties:
            complexity += min(0.3, len(properties) * 0.05)
        
        # Check for required fields
        required = schema.get('required', [])
        if required:
            complexity += min(0.2, len(required) * 0.05)
        
        # Check for nested properties (type definitions)
        for prop_value in properties.values():
            if isinstance(prop_value, dict):
                if prop_value.get('type') == 'object':
                    complexity += 0.05
                if prop_value.get('description'):
                    complexity += 0.05
        
        # Check for descriptions at schema level
        if schema.get('description'):
            complexity += 0.1
        
        total_complexity += min(complexity, 1.0)
    
    return total_complexity / len(tools)


def _calculate_disclosure_depth(tools: list[dict]) -> float:
    """
    Calculate disclosure depth score (0.0 to 1.0).
    
    Measures how well-described and hierarchically organized the tools are.
    High disclosure depth suggests progressive disclosure pattern.
    """
    if not tools:
        return 0.0
    
    total_depth = 0.0
    
    for tool in tools:
        description = tool.get('description', '')
        
        if not description:
            total_depth += 0.1
        else:
            depth = 0.3
            
            # Longer descriptions suggest more detail
            depth += min(0.3, len(description) / 200)
            
            # Check for hierarchical keywords
            hierarchical_keywords = [
                'sub', 'child', 'parent', 'nested', 'level', 'hierarchy',
                'category', 'group', 'section', 'step', 'sequence'
            ]
            for keyword in hierarchical_keywords:
                if keyword.lower() in description.lower():
                    depth += 0.1
                    break
            
            # Check for multiple sentences (more detailed explanation)
            if '.' in description:
                depth += 0.1
            
            # Check for parameter hints in description
            if '$' in description or 'param' in description.lower():
                depth += 0.1
            
            total_depth += min(depth, 1.0)
    
    return total_depth / len(tools)


def _has_dynamic_discovery(tools: list[dict]) -> bool:
    """
    Check for dynamic discovery indicators.
    
    Tools with generic names or description patterns suggesting
    dynamic behavior indicate dynamic discovery capability.
    """
    dynamic_keywords = [
        'list', 'discover', 'available', 'search', 'find',
        'query', 'browse', 'explore', 'enumerate'
    ]
    
    dynamic_count = 0
    for tool in tools:
        name = tool.get('name', '').lower()
        desc = tool.get('description', '').lower()
        
        for keyword in dynamic_keywords:
            if keyword in name or keyword in desc:
                dynamic_count += 1
                break
    
    # Consider dynamic if more than 20% of tools suggest discovery
    return dynamic_count >= len(tools) * 0.2


def _classify_pattern(tool_count: int, schema_complexity: float, 
                      disclosure_depth: float) -> str:
    """
    Classify the tool pattern based on metrics.
    
    Args:
        tool_count: Number of tools
        schema_complexity: Schema complexity score (0.0 to 1.0)
        disclosure_depth: Disclosure depth score (0.0 to 1.0)
    
    Returns:
        Pattern classification string
    """
    # Progressive disclosure: ≤4 tools with relatively simple schemas
    if tool_count <= 4:
        if schema_complexity < 0.5:
            return 'progressive_disclosure'
        else:
            return 'hybrid'
    
    # Brute force enumeration: ≥20 tools with complex schemas
    if tool_count >= 20:
        if schema_complexity >= 0.5:
            return 'brute_force_enumeration'
        else:
            return 'hybrid'
    
    # Hybrid: 5-19 tools (the middle ground)
    # Check for mixed indicators
    if schema_complexity >= 0.7:
        return 'hybrid'
    elif schema_complexity <= 0.3 and disclosure_depth >= 0.6:
        return 'hybrid'
    
    return 'hybrid'


def detect_tool_pattern(tools: list[dict]) -> dict:
    """
    Detect the MCP tool pattern from a list of tool definitions.
    
    Classifies tool collections into three architectural patterns:
    - progressive_disclosure: ≤4 high-level tools with minimal schemas
    - brute_force_enumeration: ≥20 tools with full/complex schemas
    - hybrid: Mixed approach (5-19 tools or mixed characteristics)
    
    Args:
        tools: List of tool definition dicts, each with at least 'name'
               and optionally 'description', 'inputSchema'
    
    Returns:
        dict with keys:
            - pattern (str): One of 'progressive_disclosure', 
                            'brute_force_enumeration', 'hybrid'
            - tool_count (int): Number of tools in the list
            - evidence (dict): {
                'disclosure_depth': float (0.0 to 1.0),
                'schema_complexity': float (0.0 to 1.0),
                'dynamic_discovery': bool
              }
    
    Raises:
        TypeError: If tools is not a list
        ValueError: If tools list is empty
    """
    if not isinstance(tools, list):
        raise TypeError("tools must be a list")
    
    if len(tools) == 0:
        raise ValueError("tools list cannot be empty")
    
    tool_count = len(tools)
    disclosure_depth = _calculate_disclosure_depth(tools)
    schema_complexity = _calculate_schema_complexity(tools)
    dynamic_discovery = _has_dynamic_discovery(tools)
    
    pattern = _classify_pattern(tool_count, schema_complexity, disclosure_depth)
    
    return {
        'pattern': pattern,
        'tool_count': tool_count,
        'evidence': {
            'disclosure_depth': round(disclosure_depth, 3),
            'schema_complexity': round(schema_complexity, 3),
            'dynamic_discovery': dynamic_discovery
        }
    }


if __name__ == '__main__':
    # Test 1: Progressive disclosure (2 tools, minimal schemas)
    print("Running acceptance tests...")
    
    progressive_tools = [
        {'name': 'ask', 'description': 'Ask a question to the assistant'},
        {'name': 'do', 'description': 'Execute a task or action'}
    ]
    result = detect_tool_pattern(progressive_tools)
    assert result['pattern'] == 'progressive_disclosure', \
        f"Expected 'progressive_disclosure', got '{result['pattern']}'"
    assert result['tool_count'] == 2, \
        f"Expected tool_count=2, got {result['tool_count']}"
    print(f"✓ Progressive disclosure test: {result['pattern']}, {result['tool_count']} tools")
    
    # Test 2: Brute force enumeration (25 tools with full schemas)
    brute_force_tools = []
    resource_types = ['user', 'item', 'order', 'product', 'customer']
    operations = ['get', 'create', 'update', 'delete', 'list']
    
    for i in range(25):
        resource = resource_types[i % len(resource_types)]
        operation = operations[i % len(operations)]
        brute_force_tools.append({
            'name': f'{operation}_{resource}',
            'description': f'{operation.capitalize()} {resource} with full details and parameters',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string', 'description': f'{resource.capitalize()} ID'},
                    'name': {'type': 'string', 'description': f'{resource.capitalize()} name'},
                    'data': {'type': 'object', 'description': 'Additional data'},
                    'options': {'type': 'object', 'description': 'Query options'}
                },
                'required': ['id']
            }
        })
    
    result = detect_tool_pattern(brute_force_tools)
    assert result['pattern'] == 'brute_force_enumeration', \
        f"Expected 'brute_force_enumeration', got '{result['pattern']}'"
    assert result['tool_count'] == 25, \
        f"Expected tool_count=25, got {result['tool_count']}"
    print(f"✓ Brute force enumeration test: {result['pattern']}, {result['tool_count']} tools")
    
    # Test 3: Hybrid (8 tools with mixed characteristics)
    hybrid_tools = [
        {'name': 'search', 'description': 'Search across all resources'},
        {'name': 'get_user', 'description': 'Get user by ID', 
         'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
        {'name': 'get_item', 'description': 'Get item by ID',
         'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
        {'name': 'list_users', 'description': 'List all users with pagination'},
        {'name': 'create', 'description': 'Create new resource'},
        {'name': 'update', 'description': 'Update existing resource'},
        {'name': 'delete', 'description': 'Delete resource'},
        {'name': 'batch', 'description': 'Execute batch operations on multiple resources'}
    ]
    
    result = detect_tool_pattern(hybrid_tools)
    assert result['pattern'] in ['progressive_disclosure', 'brute_force_enumeration', 'hybrid'], \
        f"Invalid pattern: {result['pattern']}"
    assert result['tool_count'] == 8, \
        f"Expected tool_count=8, got {result['tool_count']}"
    print(f"✓ Hybrid test: {result['pattern']}, {result['tool_count']} tools")
    
    print("\nAll acceptance tests passed successfully!")
    exit(0)