import os
import sys
import inspect
from importlib import import_module
from sqlalchemy import create_engine, inspect as sql_inspect

# Configuration
SIGNAL_ANALYSER_PATH = 'path/to/signal_analyser.py'
ENRICHMENTS = [
    'tool_description_safety_signal_enrichment',
    'known_bad_pattern_diversity_enrichment_v3',
    'tool_count_diversity_enrichment_v3'
]
DB_URI = 'postgresql://user:password@localhost:5432/dbname'

def get_imported_modules(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
    tree = ast.parse(content)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
    return imports

def check_enrichment_imports(signal_analyser_path, enrichments):
    imported_modules = get_imported_modules(signal_analyser_path)
    missing_imports = []
    for enrichment in enrichments:
        if enrichment not in imported_modules:
            missing_imports.append(enrichment)
    return missing_imports

def check_compute_score_outputs(enrichments):
    missing_outputs = []
    for enrichment in enrichments:
        module = import_module(enrichment)
        if not hasattr(module, 'compute_score'):
            missing_outputs.append(f"{enrichment} has no compute_score function")
            continue
        sig = inspect.signature(module.compute_score)
        if 'mcp_signal_enrichments' not in str(sig.parameters):
            missing_outputs.append(f"{enrichment}.compute_score does not write to mcp_signal_enrichments")
    return missing_outputs

def check_db_columns(db_uri):
    engine = create_engine(db_uri)
    inspector = sql_inspect(engine)
    columns = inspector.get_columns('mcp_signal_enrichments')
    return columns

def query_mcp_signal_enrichments(db_uri):
    engine = create_engine(db_uri)
    with engine.connect() as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM mcp_signal_enrichments").scalar()
        distinct_signal_types = conn.execute("SELECT DISTINCT signal_type FROM mcp_signal_enrichments").fetchall()
    return row_count, distinct_signal_types

def main():
    # Check enrichment imports
    missing_imports = check_enrichment_imports(SIGNAL_ANALYSER_PATH, ENRICHMENTS)
    if missing_imports:
        print("Missing imports in signal_analyser.py:")
        for imp in missing_imports:
            print(f"- {imp}")
    else:
        print("All enrichments are imported in signal_analyser.py")

    # Check compute_score outputs
    missing_outputs = check_compute_score_outputs(ENRICHMENTS)
    if missing_outputs:
        print("Missing compute_score outputs:")
        for output in missing_outputs:
            print(f"- {output}")
    else:
        print("All enrichments write to mcp_signal_enrichments")

    # Check DB columns
    columns = check_db_columns(DB_URI)
    print("\nColumns in mcp_signal_enrichments:")
    for column in columns:
        print(f"- {column['name']}")

    # Query mcp_signal_enrichments
    row_count, distinct_signal_types = query_mcp_signal_enrichments(DB_URI)
    print(f"\nRow count in mcp_signal_enrichments: {row_count}")
    print("Distinct signal_type values:")
    for signal_type in distinct_signal_types:
        print(f"- {signal_type[0]}")

if __name__ == "__main__":
    main()