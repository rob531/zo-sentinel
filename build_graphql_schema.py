#!/usr/bin/env python3
"""
build_graphql_schema - Generates GraphQL schema file for sentinel_external_api.md
Reads table schemas from DB_SCHEMA.md and outputs graphql_schema.py
"""

import re
import os
from pathlib import Path

def extract_table_columns(db_schema_path):
    """Extract column names from DB_SCHEMA.md"""
    tables = {}
    current_table = None
    current_columns = []
    in_columns = False
    
    with open(db_schema_path, 'r') as f:
        content = f.read()
    
    # Parse table definitions
    for line in content.split('\n'):
        line = line.strip()
        
        # Match table header like ## mcp_server_registry
        if line.startswith('## '):
            if current_table and current_columns:
                tables[current_table] = current_columns
            current_table = line[3:].strip()
            current_columns = []
            in_columns = False
        elif line.startswith('|') and current_table:
            # Parse column row
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2 and parts[1]:
                col_name = parts[1]
                # Skip separator rows and header rows
                if col_name not in ['', 'Column', '---'] and not col_name.startswith('-'):
                    current_columns.append(col_name)
    
    if current_table and current_columns:
        tables[current_table] = current_columns
    
    return tables


def generate_graphql_schema(tables):
    """Generate GraphQL schema definitions"""
    
    schema_types = []
    
    # mcp_server_registry type
    if 'mcp_server_registry' in tables:
        cols = tables['mcp_server_registry']
        fields = '\n    '.join([f'{col}: String' for col in cols])
        schema_types.append(f'''type McpServer {{
    {fields}
}}''')
    
    # mcp_signal_scores type
    if 'mcp_signal_scores' in tables:
        cols = tables['mcp_signal_scores']
        fields = '\n    '.join([f'{col}: String' for col in cols])
        schema_types.append(f'''type SignalScore {{
    {fields}
}}''')
    
    # mcp_attestations type
    if 'mcp_attestations' in tables:
        cols = tables['mcp_attestations']
        fields = '\n    '.join([f'{col}: String' for col in cols])
        schema_types.append(f'''type Attestation {{
    {fields}
}}''')
    
    # mcp_threat_associations type
    if 'mcp_threat_associations' in tables:
        cols = tables['mcp_threat_associations']
        fields = '\n    '.join([f'{col}: String' for col in cols])
        schema_types.append(f'''type ThreatAssociation {{
    {fields}
}}''')
    
    return '\n\n'.join(schema_types)


def generate_resolvers():
    """Generate resolver stub functions"""
    return '''from graphql import (
    GraphQLSchema,
    GraphQLObjectType,
    GraphQLString,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLInputObjectType,
    GraphQLField,
    GraphQLArgument
)
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def get_mcp_by_name(name):
    """Resolver for mcpByName query"""
    try:
        response = requests.post(
            WRITE_SERVICE_URL + "/query",
            json={"sql": f"SELECT * FROM mcp_server_registry WHERE name = '{name}' LIMIT 1"},
            timeout=10
        )
        result = response.json()
        return result.get('rows', [None])[0] if result.get('rows') else None
    except Exception as e:
        print(f"Error in mcpByName resolver: {e}")
        return None


def get_mcp_list(limit=50, offset=0):
    """Resolver for mcpList query"""
    try:
        response = requests.post(
            WRITE_SERVICE_URL + "/query",
            json={"sql": f"SELECT * FROM mcp_server_registry LIMIT {limit} OFFSET {offset}"},
            timeout=10
        )
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        print(f"Error in mcpList resolver: {e}")
        return []


def get_signals_for_mcp(server_id):
    """Resolver for signalsForMcp query"""
    try:
        response = requests.post(
            WRITE_SERVICE_URL + "/query",
            json={"sql": f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}'"},
            timeout=10
        )
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        print(f"Error in signalsForMcp resolver: {e}")
        return []


def get_threat_associations(server_id):
    """Resolver for threatAssociations query"""
    try:
        response = requests.post(
            WRITE_SERVICE_URL + "/query",
            json={"sql": f"SELECT * FROM mcp_threat_associations WHERE server_id = '{server_id}'"},
            timeout=10
        )
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        print(f"Error in threatAssociations resolver: {e}")
        return []


def submit_mcp_mutation(root, info, input):
    """Resolver for submitMcp mutation"""
    try:
        name = input.get('name', '')
        url = input.get('url', '')
        description = input.get('description', '')
        
        sql = f"""INSERT INTO mcp_server_registry (name, url, description, registry_source) 
                  VALUES ('{name}', '{url}', '{description}', 'graphql_api')"""
        
        response = requests.post(
            WRITE_SERVICE_URL + "/execute",
            json={"sql": sql},
            timeout=10
        )
        result = response.json()
        return {"success": result.get('ok', False), "server_id": name}
    except Exception as e:
        print(f"Error in submitMcp resolver: {e}")
        return {"success": False, "server_id": None}


def override_verdict_mutation(root, info, server_id, verdict, reason):
    """Resolver for overrideVerdict mutation"""
    try:
        sql = f"""UPDATE mcp_server_registry SET verdict = '{verdict}', 
                  description = description || ' [Manual override: {reason}]' 
                  WHERE server_id = '{server_id}'"""
        
        response = requests.post(
            WRITE_SERVICE_URL + "/execute",
            json={"sql": sql},
            timeout=10
        )
        result = response.json()
        return {"success": result.get('ok', False)}
    except Exception as e:
        print(f"Error in overrideVerdict resolver: {e}")
        return {"success": False}
'''


def generate_type_definitions(schema_types):
    """Generate GraphQL type definitions string"""
    return f'''# GraphQL Schema for ZO-SENTINEL External API
# Auto-generated by build_graphql_schema.py

{schema_types}

type McpListResult {{
    items: [McpServer]
    total: Int
}}

type MutationResult {{
    success: Boolean!
    message: String
}}

type SubmitResult {{
    success: Boolean!
    server_id: String
}}

input McpInput {{
    name: String!
    url: String!
    description: String
}}

type Query {{
    mcpByName(name: String!): McpServer
    mcpList(limit: Int, offset: Int): [McpServer]
    signalsForMcp(serverId: String!): [SignalScore]
    threatAssociations(serverId: String!): [ThreatAssociation]
}}

type Mutation {{
    submitMcp(input: McpInput!): SubmitResult
    overrideVerdict(serverId: String!, verdict: String!, reason: String!): MutationResult
}}
'''


def main():
    # Paths
    db_schema_path = '/home/workspace/DB_SCHEMA.md'
    output_dir = Path('/home/workspace/zo_sentinel')
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'graphql_schema.py'
    
    print(f"Reading table schemas from {db_schema_path}...")
    tables = extract_table_columns(db_schema_path)
    
    print(f"Found tables: {list(tables.keys())}")
    
    # Generate schema types
    schema_types = generate_graphql_schema(tables)
    type_defs = generate_type_definitions(schema_types)
    resolvers = generate_resolvers()
    
    # Write output file
    output_content = f'''"""
GraphQL Schema for ZO-SENTINEL External API
Auto-generated file - DO NOT EDIT directly
"""

# Type Definitions
TYPE_DEFINITIONS = """
{type_defs}
"""

{resolvers}

# Schema building functions
def build_schema():
    """Build GraphQL schema with resolvers"""
    from graphql import build_schema as gs_build
    
    schema = gs_build(TYPE_DEFINITIONS)
    
    # Attach resolvers to Query
    query_type = schema.query_type
    if query_type:
        query_fields = query_type.fields
        if 'mcpByName' in query_fields:
            query_fields['mcpByName'].resolve = get_mcp_by_name
        if 'mcpList' in query_fields:
            query_fields['mcpList'].resolve = get_mcp_list
        if 'signalsForMcp' in query_fields:
            query_fields['signalsForMcp'].resolve = get_signals_for_mcp
        if 'threatAssociations' in query_fields:
            query_fields['threatAssociations'].resolve = get_threat_associations
    
    # Attach resolvers to Mutation
    mutation_type = schema.mutation_type
    if mutation_type:
        mutation_fields = mutation_type.fields
        if 'submitMcp' in mutation_fields:
            mutation_fields['submitMcp'].resolve = submit_mcp_mutation
        if 'overrideVerdict' in mutation_fields:
            mutation_fields['overrideVerdict'].resolve = override_verdict_mutation
    
    return schema


if __name__ == '__main__':
    schema = build_schema()
    print("GraphQL schema built successfully")
    print(TYPE_DEFINITIONS)
'''
    
    with open(output_path, 'w') as f:
        f.write(output_content)
    
    print(f"GraphQL schema written to {output_path}")
    print("Build complete!")


if __name__ == '__main__':
    main()