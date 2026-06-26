import importlib.util
import sys
import json
from pathlib import Path

def load_module_from_file(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

def validate_schema(schema):
    required_types = ['MCP', 'Query']
    required_queries = ['mcp']

    # Check for required types
    for type_name in required_types:
        if type_name not in schema:
            raise AssertionError(f"Required type '{type_name}' not found in schema")

    # Check for required queries
    query_type = schema.get('Query', {})
    for query in required_queries:
        if query not in query_type:
            raise AssertionError(f"Required query '{query}' not found in Query type")

    print("PASS")

def main():
    try:
        # Load the graphql_schema_builder module
        module_path = Path(__file__).parent / 'graphql_schema_builder.py'
        graphql_builder = load_module_from_file('graphql_schema_builder', str(module_path))

        # Generate the schema
        schema = graphql_builder.generate_schema()

        # Validate the schema
        validate_schema(schema)

    except Exception as e:
        print(f"FAIL: {str(e)}")
        raise

if __name__ == "__main__":
    main()