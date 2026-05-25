#!/usr/bin/env python3
"""
diagnose_import_chain_smoke.py -- ZO-SENTINEL
Diagnostic module to identify import chain failures in protected smoke targets.
Target files: registry_api.py, rug_pull_monitor.py, signal_analyser.py
DOES NOT modify any target files.
Exit code 0 = diagnostic ran successfully (regardless of findings).
"""

import sys
import os
import traceback
import ast
import importlib
import inspect
import logging

SERVICE_NAME = 'diagnose_import_chain_smoke'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TARGET_FILES = [
    '/home/workspace/zo_sentinel/registry_api.py',
    '/home/workspace/zo_sentinel/rug_pull_monitor.py',
    '/home/workspace/zo_sentinel/signal_analyser.py',
]

REQUIRED_CONSTANTS = ['SERVICE_NAME', 'WRITE_SERVICE_URL']
PROTECTED_PATTERNS = ['registry_api.py', 'rug_pull_monitor.py', 'signal_analyser.py']


def check_file_exists(path):
    """Verify target file exists."""
    if not os.path.exists(path):
        return False, f"File does not exist: {path}"
    return True, "OK"


def check_syntax(path):
    """Parse Python file for syntax errors."""
    try:
        with open(path, 'r') as f:
            source = f.read()
        ast.parse(source)
        return True, "OK"
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"


def check_imports(path):
    """Attempt to import the module and capture any import errors."""
    module_name = os.path.basename(path).replace('.py', '')
    result = {
        'success': False,
        'error_type': None,
        'error_message': None,
        'traceback': None,
        'line_10_content': None,
        'line_10_suspect': False,
    }
    
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
        
        if len(lines) >= 10:
            result['line_10_content'] = lines[9].rstrip()
            if 'import' in lines[9].lower() or 'from' in lines[9].lower():
                result['line_10_suspect'] = True
        
        module = importlib.import_module(module_name)
        result['success'] = True
        result['error_message'] = "Import successful"
        return result
        
    except ImportError as e:
        result['error_type'] = 'ImportError'
        result['error_message'] = str(e)
        result['traceback'] = traceback.format_exc()
        return result
        
    except ModuleNotFoundError as e:
        result['error_type'] = 'ModuleNotFoundError'
        result['error_message'] = str(e)
        result['traceback'] = traceback.format_exc()
        return result
        
    except Exception as e:
        result['error_type'] = type(e).__name__
        result['error_message'] = str(e)
        result['traceback'] = traceback.format_exc()
        return result


def check_required_constants(path):
    """Verify required constants are defined in the module."""
    module_name = os.path.basename(path).replace('.py', '')
    findings = {}
    
    try:
        module = importlib.import_module(module_name)
        for const in REQUIRED_CONSTANTS:
            if hasattr(module, const):
                value = getattr(module, const)
                if isinstance(value, str) and 'localhost' in value:
                    findings[const] = f"FOUND: {value}"
                else:
                    findings[const] = f"FOUND: (type={type(value).__name__})"
            else:
                findings[const] = "MISSING"
        return True, findings
    except Exception as e:
        return False, {const: f"Cannot check (import failed): {e}" for const in REQUIRED_CONSTANTS}


def check_cyclic_imports(path):
    """Detect potential circular import patterns."""
    suspects = []
    
    try:
        with open(path, 'r') as f:
            source = f.read()
        
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and 'zo_sentinel' in node.module:
                    for alias in node.names:
                        full_import = f"{node.module}.{alias.name}"
                        suspects.append(f"from {node.module} import {alias.name}")
        
        if suspects:
            return suspects
        return []
        
    except Exception as e:
        return [f"Cannot parse for imports: {e}"]


def check_zo_mcp_server_imports():
    """Check if zo_mcp_server.py has the MCP tools that might be imported."""
    mcp_path = '/home/workspace/zo_sentinel/zo_mcp_server.py'
    findings = {}
    
    if os.path.exists(mcp_path):
        findings['zo_mcp_server_exists'] = True
        try:
            with open(mcp_path, 'r') as f:
                content = f.read()
            
            findings['has_mcp_instance'] = 'mcp = FastMCP(' in content
            findings['has_app'] = 'app = FastAPI()' in content or 'FastAPI(' in content
            
            import_re = __import__('re')
            findings['tool_decorators'] = len(import_re.findall(r'@mcp\.tool\(\)', content))
            findings['app_tool_decorators'] = len(import_re.findall(r'@app\.tool\(\)', content))
            
        except Exception as e:
            findings['parse_error'] = str(e)
    else:
        findings['zo_mcp_server_exists'] = False
    
    return findings


def generate_report():
    """Run all diagnostics and generate a consolidated report."""
    report = {
        'diagnostic_run': True,
        'targets_checked': [],
        'summary': {
            'total': len(TARGET_FILES),
            'syntax_errors': 0,
            'import_errors': 0,
            'missing_constants': 0,
            'circular_suspects': 0,
        },
        'details': {},
        'mcp_server_findings': check_zo_mcp_server_imports(),
        'recommendations': [],
    }
    
    logger.info("=" * 60)
    logger.info("ZO-SENTINEL Import Chain Diagnostic")
    logger.info("=" * 60)
    
    for target in TARGET_FILES:
        filename = os.path.basename(target)
        logger.info(f"\nDiagnosing: {filename}")
        
        detail = {'file': target}
        
        exists_ok, exists_msg = check_file_exists(target)
        detail['exists'] = exists_ok
        if not exists_ok:
            logger.error(f"  [FAIL] {exists_msg}")
            report['summary']['total'] -= 1
            continue
        
        syntax_ok, syntax_msg = check_syntax(target)
        detail['syntax'] = syntax_ok
        if not syntax_ok:
            report['summary']['syntax_errors'] += 1
            logger.error(f"  [SYNTAX] {syntax_msg}")
        else:
            logger.info(f"  [SYNTAX] OK")
        
        import_result = check_imports(target)
        detail['import'] = import_result
        
        if import_result['success']:
            logger.info(f"  [IMPORT] OK - module loaded successfully")
        else:
            report['summary']['import_errors'] += 1
            err_type = import_result['error_type']
            err_msg = import_result['error_message']
            logger.error(f"  [IMPORT] {err_type}: {err_msg}")
            
            if import_result['line_10_suspect']:
                logger.warning(f"  [SUSPECT] Line 10 contains import: {import_result['line_10_content']}")
            
            if 'zo_mcp_server' in str(err_msg).lower():
                report['recommendations'].append(f"{filename}: Imports zo_mcp_server - verify mcp instance name is 'mcp'")
        
        const_ok, const_results = check_required_constants(target)
        detail['constants'] = const_results
        missing = [k for k, v in const_results.items() if v == "MISSING"]
        if missing:
            report['summary']['missing_constants'] += len(missing)
            logger.warning(f"  [CONSTANTS] Missing: {missing}")
        else:
            logger.info(f"  [CONSTANTS] All found: {const_results}")
        
        cyclic = check_cyclic_imports(target)
        detail['cyclic_suspects'] = cyclic
        if cyclic:
            report['summary']['circular_suspects'] += len(cyclic)
            logger.warning(f"  [CYCLIC] Potential circular imports detected:")
            for c in cyclic:
                logger.warning(f"    - {c}")
        
        report['details'][filename] = detail
        report['targets_checked'].append(filename)
    
    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Targets checked: {len(report['targets_checked'])}")
    logger.info(f"Syntax errors: {report['summary']['syntax_errors']}")
    logger.info(f"Import errors: {report['summary']['import_errors']}")
    logger.info(f"Missing constants: {report['summary']['missing_constants']}")
    logger.info(f"Circular suspects: {report['summary']['circular_suspects']}")
    
    logger.info(f"\nzo_mcp_server.py findings:")
    for k, v in report['mcp_server_findings'].items():
        logger.info(f"  {k}: {v}")
    
    if report['recommendations']:
        logger.info("\nRECOMMENDATIONS:")
        for i, rec in enumerate(report['recommendations'], 1):
            logger.info(f"  {i}. {rec}")
    
    critical_imports = {
        'from zo_sentinel.zo_mcp_server import mcp': 'zo_mcp_server.py must export "mcp" instance',
        'from zo_sentinel.zo_mcp_server import app': 'zo_mcp_server.py may export "app" if app-level decorators used',
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("ROOT CAUSE HYPOTHESIS")
    logger.info("=" * 60)
    
    if report['summary']['import_errors'] > 0:
        for filename, details in report['details'].items():
            if not details['import']['success']:
                err = details['import']['error_message']
                if 'zo_mcp_server' in err.lower():
                    logger.error(f"\n[{filename}] Likely cause: zo_mcp_server.py import chain broken")
                    logger.error("  - Protected files may be importing from zo_mcp_server before it initializes")
                    logger.error("  - Check if zo_mcp_server.py has 'mcp = FastMCP()' at module level")
                    logger.error("  - If protected files use '@app.tool()' instead of '@mcp.tool()', this will fail")
                    report['recommendations'].append(
                        f"{filename}: If importing from zo_mcp_server, use 'from zo_sentinel.zo_mcp_server import mcp' "
                        "and decorators must be '@mcp.tool()', NOT '@app.tool()'"
                    )
                elif 'line 10' in str(err).lower():
                    logger.error(f"\n[{filename}] Specific issue at line 10 (as reported by smoke)")
                    if details['import']['line_10_suspect']:
                        logger.error(f"  Line 10 content: {details['import']['line_10_content']}")
                        logger.error("  This line contains an import statement that may be failing")
    
    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSTIC COMPLETE - NOT modifying any protected files")
    logger.info("=" * 60)
    
    return report


def main():
    """Entry point."""
    logger.info("Starting import chain diagnostic...")
    
    try:
        report = generate_report()
        
        if report['summary']['import_errors'] > 0:
            logger.warning(f"\nFound {report['summary']['import_errors']} import errors that need resolution")
            for filename in report['targets_checked']:
                detail = report['details'].get(filename, {})
                imp = detail.get('import', {})
                if not imp.get('success'):
                    logger.warning(f"  - {filename}: {imp.get('error_type')} - {imp.get('error_message')}")
        
        logger.info("\nDiagnostic completed successfully. Exit code 0.")
        sys.exit(0)
        
    except Exception as e:
        logger.exception(f"Diagnostic failed with exception: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()