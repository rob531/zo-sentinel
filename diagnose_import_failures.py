import os
import sys
from typing import List
import importlib.util
import importlib.machinery
import pkg_resources

def check_sys_path() -> bool:
    return os.pathsep in sys.path

def read_import_chains() -> List[str]:
    chains = []
    for module in sys.modules.values():
        if hasattr(module, '__file__'):
            chain = os.path.dirname(module.__file__)
            chains.append(chain)
    return chains

def check_sys_path_imports(chains: List[str]) -> bool:
    for chain in chains:
        spec = importlib.util.find_spec(os.path.join(chain, '__init__.py'))
        if spec is None:
            return False
    return True

def check_dependency_availability() -> bool:
    try:
        pkg_resources.get_distribution('ZO-SENTINEL')
    except pkg_resources.DistributionNotFound:
        return False
    return True

def diagnose_smoke_import_failures() -> None:
    import chains
    if not check_sys_path():
        print("System Path is Not Set")
        return
    chains = read_import_chains()
    if not check_sys_path_imports(chains):
        print("Import Chain is Corrupted")
        return
    if not check_dependency_availability():
        print("Missing ZO-SENTINEL Dependency")
        return

    print("\nDiagnostic Report:")
    print("--------------------")
    print(f"System Path: {os.pathsep}")
    print(f"Import Chains: {chains}")
    print(f"Dependency Availability: {check_dependency_availability()}")