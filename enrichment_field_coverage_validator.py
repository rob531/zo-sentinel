import ast
from pathlib import Path
from typing import Dict, List, Set


class MetadataFieldVisitor(ast.NodeVisitor):
    """AST visitor to detect metadata field access patterns in enrichment modules."""
    
    METADATA_VAR_NAMES = {'metadata', 'meta', 'data', 'info', 'record', 'server', 'server_data', 'm'}
    IGNORED_VAR_NAMES = {'self', 'cls', 'result', 'response', 'config', 'options', 'args', 'kwargs'}
    
    def __init__(self):
        self.fields_used: List[str] = []
        self.metadata_vars: Set[str] = set()
        self.function_locals: Set[str] = set()
        self.in_assign_target: bool = False
    
    def visit_Assign(self, node):
        """Track variable assignments to identify metadata sources."""
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id not in self.IGNORED_VAR_NAMES:
                self.function_locals.add(target.id)
        self.generic_visit(node)
    
    def visit_Subscript(self, node):
        """Detect metadata field access via subscript notation like metadata['field']."""
        self._check_metadata_access(node.value)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            field = node.slice.value.strip()
            if field and not field.startswith('_'):
                self.fields_used.append(field)
        self.generic_visit(node)
    
    def visit_Attribute(self, node):
        """Detect metadata field access via attribute notation like metadata.field."""
        self._check_metadata_access(node.value)
        if isinstance(node.ctx, ast.Load):
            attr = node.attr
            if attr and not attr.startswith('_'):
                self.fields_used.append(attr)
        self.generic_visit(node)
    
    def _check_metadata_access(self, node):
        """Check if a node represents a metadata variable access."""
        if isinstance(node, ast.Name):
            var_name = node.id
            if var_name in self.METADATA_VAR_NAMES or var_name in self.function_locals:
                self.metadata_vars.add(var_name)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                var_name = node.value.id
                if var_name in self.METADATA_VAR_NAMES:
                    self.metadata_vars.add(var_name)
    
    def visit_Name(self, node):
        """Track local variable names."""
        if isinstance(node.ctx, ast.Store):
            self.function_locals.add(node.id)
        self.generic_visit(node)
    
    def visit_Call(self, node):
        """Track function calls for field extraction patterns."""
        if isinstance(node.func, ast.Name):
            self.function_locals.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                self.function_locals.add(node.func.value.id)
        self.generic_visit(node)
    
    def visit_If(self, node):
        """Track conditional checks on metadata fields."""
        if isinstance(node.test, ast.Compare):
            for comp in node.test.comparators:
                self._check_metadata_access(comp)
        elif isinstance(node.test, ast.Name):
            self.function_locals.add(node.test.id)
        elif isinstance(node.test, ast.Attribute):
            self._check_metadata_access(node.test.value)
            if isinstance(node.test.value, ast.Name):
                self.function_locals.add(node.test.value.id)
        self.generic_visit(node)
    
    def visit_For(self, node):
        """Track iteration variables over metadata."""
        if isinstance(node.target, ast.Name):
            self.function_locals.add(node.target.id)
        self.generic_visit(node)


def validate_enrichment(module_path: str) -> Dict:
    """
    Validate that an enrichment module reads multiple metadata fields.
    
    Args:
        module_path: Path to the enrichment module file
        
    Returns:
        Dict with keys:
            - field_count: total number of field accesses detected
            - fields_used: list of unique field names accessed
            - distinct_metadata_keys: set of distinct field names
            - coverage_score: normalized score 0.0-1.0
                              >= 0.7 requires >= 3 distinct metadata fields
    """
    path = Path(module_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Module not found: {module_path}")
    
    try:
        source = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        source = path.read_text(encoding='latin-1')
    
    tree = ast.parse(source, filename=str(path))
    
    visitor = MetadataFieldVisitor()
    visitor.visit(tree)
    
    fields_used_set = set(visitor.fields_used)
    distinct_metadata_keys = fields_used_set.copy()
    
    field_count = len(visitor.fields_used)
    distinct_count = len(fields_used_set)
    
    coverage_score = min(1.0, distinct_count / 6.0)
    
    if distinct_count >= 3:
        coverage_score = max(coverage_score, 0.7)
    
    return {
        'field_count': field_count,
        'fields_used': sorted(list(fields_used_set)),
        'distinct_metadata_keys': distinct_metadata_keys,
        'coverage_score': coverage_score
    }


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        result = validate_enrichment(sys.argv[1])
        print(f"Coverage Score: {result['coverage_score']:.2f}")
        print(f"Field Count: {result['field_count']}")
        print(f"Distinct Fields: {len(result['distinct_metadata_keys'])}")
        print(f"Fields: {result['fields_used']}")
        if result['coverage_score'] >= 0.7:
            print("PASS: Field coverage meets threshold")
        else:
            print("FAIL: Field coverage below threshold (requires >= 3 distinct fields)")
    else:
        print("Usage: python enrichment_field_coverage_validator.py <module_path>")