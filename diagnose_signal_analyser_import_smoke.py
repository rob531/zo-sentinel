#!/usr/bin/env python3
"""
diagnose_signal_analyser_import_smoke.py
Diagnostic module for smoke failure in signal_analyser.py showing import traceback at line 10.
"""
import sys
import traceback

REQUIRED_IMPORTS = [
    ("logging", "stdlib"),
    ("re", "stdlib"),
    ("datetime", "stdlib"),
    ("timezone", "datetime"),
    ("typing", "stdlib"),
    ("Optional", "typing"),
    ("List", "typing"),
    ("Dict", "typing"),
]

def test_import(module_name, sub_name=None):
    """Test importing a module or attribute."""
    result = {"module": module_name, "sub": sub_name, "success": False, "error": None}
    try:
        if sub_name:
            mod = __import__(module_name, fromlist=[sub_name])
            getattr(mod, sub_name)
        else:
            __import__(module_name)
        result["success"] = True
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
    return result

def main():
    print("=" * 60)
    print("ZO-SENTINEL Import Smoke Diagnostic")
    print("Target: signal_analyser.py line 10 imports")
    print("=" * 60)
    
    all_passed = True
    for module_name, sub_name in REQUIRED_IMPORTS:
        if sub_name:
            full_name = f"{module_name}.{sub_name}"
        else:
            full_name = module_name
        
        result = test_import(module_name, sub_name)
        status = "PASS" if result["success"] else "FAIL"
        if not result["success"]:
            all_passed = False
        
        print(f"\n[{status}] {full_name}")
        if result["error"]:
            print(f"  Error: {result['error']}")
            if "traceback" in result:
                print(f"  Traceback:\n{result['traceback']}")
    
    print("\n" + "=" * 60)
    if all_passed:
        print("RESULT: All imports OK - problem may be elsewhere")
        return 0
    else:
        print("RESULT: Import failures detected")
        return 1

if __name__ == "__main__":
    sys.exit(main())