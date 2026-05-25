import sys
import logging
import importlib
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

sys.path.insert(0, '/home/workspace/zo_sentinel')

def check_module_imports():
    logger.info("Checking temporal_stability_enrichment_v2 imports...")
    try:
        module = importlib.import_module('temporal_stability_enrichment_v2')
        logger.info("Module imported successfully")
    except ImportError as e:
        logger.error(f"Import failed: {e}")
        return False
    return True

def check_function_signature():
    logger.info("Checking compute_score function signature...")
    try:
        module = importlib.import_module('temporal_stability_enrichment_v2')
        func = getattr(module, 'compute_score', None)
        if func is None:
            logger.error("compute_score function not found in module")
            return False
        logger.info(f"compute_score found: {func}")
        return True
    except Exception as e:
        logger.error(f"Signature check failed: {e}")
        return False

def check_execution_time():
    logger.info("Checking execution time (target < 2s)...")
    try:
        module = importlib.import_module('temporal_stability_enrichment_v2')
        compute_score = module.compute_score

        test_metadata = {
            'last_updated': '2024-01-01',
            'first_seen': '2023-01-01',
            'download_count': 1000,
            'versions': ['1.0.0', '1.1.0'],
            'recent_update': '2024-06-01'
        }

        start = time.time()
        result = compute_score(test_metadata)
        elapsed = time.time() - start

        logger.info(f"Execution time: {elapsed:.3f}s, result: {result}")

        if elapsed > 2.0:
            logger.warning(f"Execution exceeded 2s threshold: {elapsed:.3f}s")
            return False

        return True
    except Exception as e:
        logger.error(f"Execution time check failed: {e}")
        return False

def main():
    logger.info("=== temporal_stability_enrichment_v2 smoke check ===")

    checks = [
        ("Module imports", check_module_imports),
        ("Function signature", check_function_signature),
        ("Execution time < 2s", check_execution_time),
    ]

    all_passed = True
    for name, check_fn in checks:
        logger.info(f"Running: {name}")
        if not check_fn():
            all_passed = False
            logger.error(f"FAILED: {name}")

    if all_passed:
        logger.info("=== ALL CHECKS PASSED ===")
        sys.exit(0)
    else:
        logger.error("=== SOME CHECKS FAILED ===")
        sys.exit(1)

if __name__ == '__main__':
    main()