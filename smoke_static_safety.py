import ast
import re
from typing import List, Tuple

BLOCKING_REASONS = [
    'eval_or_exec_with_external_input',
    'wrong_serialization_for_write_service',
]

WARNING_REASONS = [
    'sql_string_interpolation',
]

def lint(code: str, directive: dict) -> Tuple[bool, List[str]]:
    reasons = []
    try:
        tree = ast.parse(code)
    except Exception as e:
        return False, [f'parse_error_{type(e).__name__}']

    has_http_import = bool(
        re.search(r'\b(urllib\.request|urllib3|requests|urlopen)\b', code) or
        re.search(r'\bimport (urllib\.request|urllib3|requests)\b', code)
    )
    has_subprocess_import = 'subprocess' in code or re.search(r'\bimport subprocess\b', code)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ('eval', 'exec'):
                if has_http_import or has_subprocess_import:
                    reasons.append('eval_or_exec_with_external_input')

    http_attrs = {'post', 'put', 'patch', 'request'}
    http_names = {'requests'}
    desc_lower = directive.get('description', '').lower()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in http_attrs and isinstance(node.func.value, ast.Name):
                if node.func.value.id in http_names:
                    kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                    if 'json' not in kwarg_names and 'data' in kwarg_names:
                        if any(x in desc_lower for x in ['write_service', 'writeservice', 'json']):
                            reasons.append('wrong_serialization_for_write_service')

    pure_indicator = any(x in desc_lower for x in ['pure function', 'compute_score(metadata', 'no db writes', 'no network'])
    if pure_indicator:
        if re.search(r'^\s*while True:', code, re.MULTILINE):
            reasons.append('pure_function_contract_violated_while_true')
        if re.search(r'\bimport socket\b', code) or re.search(r'\bfrom socket\b', code):
            reasons.append('pure_function_contract_violated_socket')
        if re.search(r'\bimport requests\b', code) or re.search(r'\bfrom requests\b', code):
            reasons.append('pure_function_contract_violated_requests')
        if re.search(r'\bimport urllib\.request\b', code) or re.search(r'\bfrom urllib\.request\b', code):
            reasons.append('pure_function_contract_violated_urllib')
        if re.search(r'\bimport subprocess\b', code) or re.search(r'\bfrom subprocess\b', code):
            reasons.append('pure_function_contract_violated_subprocess')
        if re.search(r'\bimport threading\b', code) or re.search(r'\bfrom threading\b', code):
            reasons.append('pure_function_contract_violated_threading')
        if re.search(r'\bimport signal\b', code) or re.search(r'\bfrom signal\b', code):
            reasons.append('pure_function_contract_violated_signal')

    sql_keywords = ['select', 'insert', 'update', 'delete']
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for val in node.values:
                if isinstance(val, ast.FormattedValue) or isinstance(val, ast.Constant):
                    val_str = val.value if isinstance(val, ast.Constant) else ''
                    if isinstance(val, ast.FormattedValue) and hasattr(val, 'values'):
                        for v in val.values:
                            if isinstance(v, ast.Constant):
                                val_str += str(v.value)
                    if any(kw in val_str.lower() for kw in sql_keywords):
                        reasons.append('sql_string_interpolation')
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = ''
            if isinstance(node.left, ast.Constant):
                left = str(node.left.value)
            elif isinstance(node.left, ast.Str):
                left = node.left.s
            if any(kw in left.lower() for kw in sql_keywords):
                reasons.append('sql_string_interpolation')

    blocking = [r for r in reasons if r not in WARNING_REASONS]
    is_safe = len(blocking) == 0
    return is_safe, reasons

def run_tests():
    test_cases = [
        (
            "clean pure function",
            """
def compute_score(metadata):
    return metadata.get('value', 0) * 10
""",
            {'description': 'PURE FUNCTION: compute_score(metadata) -> int. No DB writes. NO network.'},
            True,
            [],
        ),
        (
            "eval-only file (no external I/O)",
            """
def dynamic_eval(code):
    return eval(code)
""",
            {'description': 'Dynamic eval utility'},
            True,
            [],
        ),
        (
            "eval + http import",
            """
import requests
def dynamic_eval(code):
    return eval(code)
""",
            {'description': 'Dynamic eval with HTTP'},
            False,
            ['eval_or_exec_with_external_input'],
        ),
        (
            "requests.post with data= and write_service mention",
            """
import requests
def send_update(payload):
    return requests.post('http://example.com', data=payload)
""",
            {'description': 'WriteService JSON endpoint integration'},
            False,
            ['wrong_serialization_for_write_service'],
        ),
        (
            "while True in a PURE FUNCTION contract",
            """
import requests
def scan_loop():
    while True:
        pass
""",
            {'description': 'PURE FUNCTION: analyze code. No DB writes. NO network.'},
            False,
            ['pure_function_contract_violated_while_true', 'pure_function_contract_violated_requests'],
        ),
        (
            "SQL f-string interpolation",
            """
def query_server(server_id):
    sql = f\"SELECT * FROM servers WHERE id = {server_id}\"
    return sql
""",
            {'description': 'SQL query builder'},
            True,
            ['sql_string_interpolation'],
        ),
    ]

    passed = 0
    failed = 0
    for name, code, directive, expected_safe, expected_reasons in test_cases:
        try:
            is_safe, reasons = lint(code, directive)
            blocking_reasons = [r for r in reasons if r not in WARNING_REASONS]
            is_safe_blocking = len(blocking_reasons) == 0

            ok = (is_safe_blocking == expected_safe) and (set(blocking_reasons) == set(expected_reasons))
            if ok:
                print(f"PASS: {name}")
                passed += 1
            else:
                print(f"FAIL: {name}")
                print(f"  Expected safe={expected_safe}, reasons={expected_reasons}")
                print(f"  Got safe={is_safe_blocking}, blocking_reasons={blocking_reasons}")
                failed += 1
        except Exception as e:
            print(f"FAIL: {name} (raised {type(e).__name__}: {e})")
            failed += 1

    print(f"\n{passed}/{passed+failed} passed")
    return failed == 0

if __name__ == '__main__':
    import sys
    sys.exit(0 if run_tests() else 1)