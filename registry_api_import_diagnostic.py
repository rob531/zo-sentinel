import ast
import sys
import subprocess
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

SERVICE_NAME = 'registry_api_import_diagnostic'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_PATH = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TARGET_FILE = '/home/workspace/zo_sentinel/registry_api.py'
ZO_SENTINEL_DIR = Path('/home/workspace/zo_sentinel')


def extract_imports(source_code):
    """Parse source and extract all import statements."""
    imports = []
    try:
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(('import', alias.name, alias.asname))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    full_name = f"{module}.{alias.name}" if module else alias.name
                    imports.append(('from', module, alias.name, alias.asname))
    except SyntaxError as e:
        logger.error(f"Syntax error parsing source: {e}")
    return imports


def test_import_in_subprocess(import_str):
    """Test a single import in an isolated subprocess."""
    test_code = f"import {import_str}"
    try:
        result = subprocess.run(
            [sys.executable, '-c', test_code],
            capture_output=True,
            text=True,
            timeout=10
        )
        success = result.returncode == 0
        error = result.stderr.strip() if not success else None
        return success, error
    except subprocess.TimeoutExpired:
        return False, "Timeout expired"
    except Exception as e:
        return False, str(e)


def find_recently_built_files():
    """Find recently built files within last 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_files = []
    
    if ZO_SENTINEL_DIR.exists():
        for py_file in ZO_SENTINEL_DIR.glob('*.py'):
            if py_file.stat().st_mtime > cutoff.timestamp():
                recent_files.append(py_file)
    
    return recent_files


def extract_conflicts(source_code):
    """Extract all imports from source code for conflict analysis."""
    return extract_imports(source_code)


def run_diagnostic():
    """Main diagnostic routine."""
    logger.info(f"Starting import diagnostic for {TARGET_FILE}")
    
    # Read the target file
    target_path = Path(TARGET_FILE)
    if not target_path.exists():
        logger.error(f"Target file not found: {TARGET_FILE}")
        return 1
    
    try:
        source_code = target_path.read_text(encoding='utf-8')
        logger.info(f"Read {len(source_code)} bytes from {TARGET_FILE}")
    except Exception as e:
        logger.error(f"Failed to read target file: {e}")
        return 1
    
    # Extract imports
    imports = extract_imports(source_code)
    logger.info(f"Found {len(imports)} import statements")
    
    failed_imports = []
    for imp in imports:
        if imp[0] == 'import':
            _, name, alias = imp
            import_str = name
        else:
            _, module, name, alias = imp
            import_str = f"{module}.{name}" if module else name
        
        logger.info(f"Testing import: {import_str}")
        success, error = test_import_in_subprocess(import_str)
        
        if not success:
            failed_imports.append({
                'import': import_str,
                'error': error
            })
            logger.warning(f"FAILED import '{import_str}': {error}")
        else:
            logger.info(f"OK: {import_str}")
    
    # Check for conflicting imports from recent files
    logger.info("Checking recently built files for conflicting imports...")
    recent_files = find_recently_built_files()
    logger.info(f"Found {len(recent_files)} recently modified files")
    
    all_known_imports = set()
    for imp in imports:
        if imp[0] == 'import':
            all_known_imports.add(imp[1])
        else:
            module = imp[1] or ''
            name = imp[2]
            all_known_imports.add(f"{module}.{name}" if module else name)
    
    conflicts = []
    for recent_file in recent_files:
        if recent_file.name == 'registry_api_import_diagnostic.py':
            continue
        try:
            recent_source = recent_file.read_text(encoding='utf-8')
            recent_imports = extract_conflicts(recent_source)
            for ri in recent_imports:
                if ri[0] == 'import':
                    key = ri[1]
                else:
                    module = ri[1] or ''
                    name = ri[2]
                    key = f"{module}.{name}" if module else name
                
                if key in all_known_imports:
                    conflicts.append({
                        'import': key,
                        'conflicting_file': str(recent_file)
                    })
                    logger.warning(f"Conflicting import '{key}' found in {recent_file.name}")
        except Exception as e:
            logger.warning(f"Could not read {recent_file}: {e}")
    
    # Summary
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total imports tested: {len(imports)}")
    logger.info(f"Failed imports: {len(failed_imports)}")
    logger.info(f"Conflicting imports: {len(conflicts)}")
    
    if failed_imports:
        logger.info("\nFAILED IMPORTS:")
        for fi in failed_imports:
            logger.info(f"  - {fi['import']}: {fi['error'][:200]}")
    
    if conflicts:
        logger.info("\nCONFLICTING IMPORTS:")
        for c in conflicts:
            logger.info(f"  - {c['import']} (in {Path(c['conflicting_file']).name})")
    
    if not failed_imports and not conflicts:
        logger.info("\nNo import issues detected.")
    
    return 0


if __name__ == '__main__':
    sys.exit(run_diagnostic())