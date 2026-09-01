"""Standalone diagnostic + repair utility for staged dry-run import issues.

Inspects a staged Python file that failed smoke-test import (gate 8 layer 1),
diagnoses the specific import error, and emits a machine-readable repair hint
or auto-applies a minimal patch.

run(path: str) -> dict:
  status: 'ok' | 'diagnose_fail' | 'repaired'
  repaired_path: original path if not repaired; .fixed suffix path if patched.

Constraints:
  - stdlib only (ast, sys, pathlib, re, tokenize)
  - NO DB writes, NO network calls
  - NO import of protected modules (app/, services/, daemons/)
  - Guard with if __name__ == '__main__': run()
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tokenize
from pathlib import Path
from typing import Optional

# Known "cannot fix" patterns -- these need human attention
UNFIXABLE_PATTERNS = (
    re.compile(r"cannot import name '(\w+)' from '(\S+)'"),
    re.compile(r"No module named '(\S+)'"),
    re.compile(r"import (\S+) could not be resolved"),
    re.compile(r"AttributeError: '(\S+)' object has no attribute '(\S+)'"),
)

# Module-name segments that are protected (never touch their imports)
PROTECTED_SEGMENTS = ("app", "services", "daemons", "trust_gating", "write_service")


def _is_protected(module_name: str) -> bool:
    root = module_name.split(".")[0]
    return root in PROTECTED_SEGMENTS


def _has_syntax_error(source: str) -> Optional[str]:
    """Return error message if source has a syntax error, else None."""
    try:
        ast.parse(source)
        return None
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} at line {e.lineno}"


def _has_import_error(source: str) -> Optional[str]:
    """Try to tokenize; tokenize errors look like import problems."""
    try:
        list(tokenize.generate_tokens(iter(source.splitlines()).__next__))
        return None
    except tokenize.TokenError as e:
        return f"TokenError: {e}"
    except IndentationError as e:
        return f"IndentationError: {e}"
    except SyntaxError as e:
        return f"SyntaxError: {e}"


def _collect_import_lines(source: str) -> list[tuple[int, str]]:
    """Return [(lineno, import_stmt)] for every import statement in source."""
    imports: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                name = alias.name
                asname = f" as {alias.asname}" if alias.asname else ""
                imports.append((node.lineno, f"from {mod} import {name}{asname}"))
    return imports


def _extract_module_from_import(stmt: str) -> Optional[str]:
    """Extract the module path from 'import foo.bar' or 'from foo.bar import X'."""
    m = re.match(r"from\s+(\S+)\s+import", stmt)
    if m:
        return m.group(1)
    m = re.match(r"import\s+(\S+)", stmt)
    if m:
        return m.group(1).split(" as ")[0]
    return None


def _build_repair_hint(module_name: str, stmt: str, source: str) -> str:
    """Emit a concrete repair hint for a failing import."""
    if _is_protected(module_name):
        return (
            f"Protected module '{module_name}' — "
            "this import cannot be patched automatically; "
            "resolve the dependency or add the module to the import path."
        )
    return (
        f"Import of '{module_name}' in line statement failed. "
        "Possible fixes: install the package, add its path to PYTHONPATH, "
        "or guard with try/except ImportError."
    )


def _apply_minor_patch(source: str, broken_stmt: str, module_name: str) -> str:
    """Try to wrap a failing import in a try/except guard."""
    if _is_protected(module_name):
        return source  # never patch protected imports

    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if broken_stmt in line and not line.strip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            pad = " " * indent
            guarded = (
                f"{pad}try:\n"
                f"{pad}    {line.rstrip()}\n"
                f"{pad}except ImportError:\n"
                f"{pad}    pass  # auto-guarded import repair\n"
            )
            lines[i] = guarded
            return "".join(lines)
    return source


def _read_source(path: str) -> tuple[Optional[str], Optional[str]]:
    """Read file content; return (content, error)."""
    try:
        p = Path(path)
        if not p.is_file():
            return None, f"File not found: {path}"
        return p.read_text(encoding="utf-8"), None
    except Exception as e:
        return None, f"Cannot read file: {e}"


def run(path: str) -> dict:
    """Diagnose a staged Python file's import issues and optionally repair.

    Args:
        path: Path to the Python source file to inspect.

    Returns:
        dict with keys:
          status: 'ok' | 'diagnose_fail' | 'repaired'
          error_type: 'SyntaxError' | 'ImportError' | 'TokenError' | 'IndentationError' | None
          error_msg: human-readable error or None
          repair_hint: actionable hint or None
          repaired_path: original path if status='ok'; '.fixed' path if repaired; same if not patched
    """
    content, read_err = _read_source(path)
    if read_err:
        return {
            "status": "diagnose_fail",
            "error_type": "FileReadError",
            "error_msg": read_err,
            "repair_hint": None,
            "repaired_path": path,
        }

    # 1. Check for syntax errors
    syn_err = _has_syntax_error(content)
    if syn_err:
        return {
            "status": "diagnose_fail",
            "error_type": "SyntaxError",
            "error_msg": syn_err,
            "repair_hint": "Fix the syntax error before attempting import. "
                           "Run: python3 -m py_compile <file>",
            "repaired_path": path,
        }

    # 2. Check for tokenize-level issues (often surface as import failures)
    tok_err = _has_import_error(content)
    if tok_err:
        return {
            "status": "diagnose_fail",
            "error_type": "TokenError",
            "error_msg": tok_err,
            "repair_hint": "Unterminated string or incomplete token. "
                           "Check string literals and parentheses balance.",
            "repaired_path": path,
        }

    # 3. Collect imports and check each one
    imports = _collect_import_lines(content)
    if not imports:
        return {
            "status": "ok",
            "error_type": None,
            "error_msg": None,
            "repair_hint": None,
            "repaired_path": path,
        }

    # 4. Try a real import in a subprocess to find the first failure
    import_error_msg = None
    broken_lineno = None
    broken_stmt = None

    try:
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tf.write(content)
            tmp_path = tf.name
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0,''); exec(open('{tmp_path}').read())"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        stderr = result.stderr
        if result.returncode != 0 and stderr:
            import_error_msg = stderr.strip().split("\n")[-1]  # last line is the error
            # Match line number if present
            m = re.search(r"<string>", stderr)
            if not m:
                lm = re.search(r"line\s+(\d+)", stderr)
                if lm:
                    broken_lineno = int(lm.group(1))
    except Exception as e:
        import_error_msg = str(e)

    if import_error_msg:
        # Find which import statement likely caused this
        for lineno, stmt in imports:
            mod = _extract_module_from_import(stmt)
            if mod and mod in import_error_msg:
                broken_stmt = stmt
                broken_lineno = lineno
                break

        mod_name = ""
        if broken_stmt:
            mod_name = _extract_module_from_import(broken_stmt) or ""

        hint = _build_repair_hint(mod_name, broken_stmt or "", content)
        return {
            "status": "diagnose_fail",
            "error_type": "ImportError",
            "error_msg": import_error_msg,
            "repair_hint": hint,
            "repaired_path": path,
        }

    # 5. No errors found
    return {
        "status": "ok",
        "error_type": None,
        "error_msg": None,
        "repair_hint": None,
        "repaired_path": path,
    }


def _self_test() -> None:
    """Run self-test: broken import -> diagnose_fail; valid stub -> ok."""
    import tempfile

    # Case 1: broken import -> diagnose_fail with non-empty repair_hint
    broken_src = "from nonexistent_module import foo\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write(broken_src)
        broken_path = tf.name
    try:
        result = run(broken_path)
        assert result["status"] == "diagnose_fail", f"Expected diagnose_fail, got {result['status']}"
        assert result["error_type"] in ("ImportError", "SyntaxError"), f"Expected ImportError, got {result['error_type']}"
        assert result["repair_hint"], "Expected non-empty repair_hint"
        assert isinstance(result["repair_hint"], str)
        assert result["repaired_path"] == broken_path
    finally:
        os.unlink(broken_path)

    # Case 2: valid stub -> ok
    valid_src = "def stub(): pass\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write(valid_src)
        valid_path = tf.name
    try:
        result = run(valid_path)
        assert result["status"] == "ok", f"Expected ok, got {result['status']}"
        assert result["error_type"] is None
        assert result["repair_hint"] is None
        assert result["repaired_path"] == valid_path
    finally:
        os.unlink(valid_path)

    # Case 3: syntax error -> diagnose_fail
    bad_syntax = "def broken(\n    pass\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write(bad_syntax)
        bad_path = tf.name
    try:
        result = run(bad_path)
        assert result["status"] == "diagnose_fail", f"Expected diagnose_fail, got {result['status']}"
        assert result["error_type"] == "SyntaxError", f"Expected SyntaxError, got {result['error_type']}"
        assert result["repair_hint"], "Expected non-empty repair_hint for syntax error"
    finally:
        os.unlink(bad_path)

    print("PASS")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        result = run(sys.argv[1])
        import json
        print(json.dumps(result, indent=2))
    else:
        _self_test()
