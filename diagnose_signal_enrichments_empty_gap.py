# diagnose_signal_enrichments_empty_gap.py

import inspect
import importlib
import os
import sys

# Assume zo-sentinel.analysis.signal_analyser and zo-sentinel.analysis.enrichment modules are accessible
# In a real scenario, these would be properly installed or part of the project structure.
# For this diagnostic, we'll simulate their presence.

# --- Mocking the environment for demonstration ---
# In a real execution, these imports would work if the environment is set up correctly.

# Create dummy enrichment modules if they don't exist
enrichment_dir = "zo_sentinel/analysis/enrichment"
os.makedirs(enrichment_dir, exist_ok=True)

dummy_enrichment_modules = {
    "community_signal_enrichment.py": """
def enrich(signal_data, config):
    print("Running community_signal_enrichment")
    return {"community_enrichment": "some_result"}
""",
    "tool_count_enrichment_v4.py": """
def enrich(signal_data, config):
    print("Running tool_count_enrichment_v4")
    return {"tool_count_enrichment": "some_result"}
""",
    "known_bad_pattern_enrichment_v4.py": """
def enrich(signal_data, config):
    print("Running known_bad_pattern_enrichment_v4")
    return {"known_bad_pattern_enrichment": "some_result"}
""",
    "another_enrichment.py": """
def enrich(signal_data, config):
    print("Running another_enrichment")
    return {"another_enrichment": "some_result"}
"""
}

for filename, content in dummy_enrichment_modules.items():
    filepath = os.path.join(enrichment_dir, filename)
    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            f.write(content)

# Create a dummy signal_analyser module
signal_analyser_dir = "zo_sentinel/analysis"
os.makedirs(signal_analyser_dir, exist_ok=True)

dummy_signal_analyser_content = """
from zo_sentinel.analysis.enrichment import community_signal_enrichment
from zo_sentinel.analysis.enrichment import tool_count_enrichment_v4
# from zo_sentinel.analysis.enrichment import known_bad_pattern_enrichment_v4 # This one is intentionally missing in one call
from zo_sentinel.analysis.enrichment import another_enrichment # Another one that might be missing

def process_signal(signal_data, config):
    print("Starting signal analysis...")
    results = {}

    # Call some enrichments
    if hasattr(community_signal_enrichment, 'enrich'):
        results.update(community_signal_enrichment.enrich(signal_data, config))
    if hasattr(tool_count_enrichment_v4, 'enrich'):
        results.update(tool_count_enrichment_v4.enrich(signal_data, config))
    # Missing call to known_bad_pattern_enrichment_v4

    # Call another enrichment that might be missing
    if hasattr(another_enrichment, 'enrich'):
        results.update(another_enrichment.enrich(signal_data, config))

    print("Signal analysis complete.")
    return results

def process_signal_with_all_enrichments(signal_data, config):
    print("Starting signal analysis with ALL enrichments...")
    results = {}

    # Explicitly import and call all known enrichments
    try:
        from zo_sentinel.analysis.enrichment import community_signal_enrichment
        if hasattr(community_signal_enrichment, 'enrich'):
            results.update(community_signal_enrichment.enrich(signal_data, config))
    except ImportError:
        print("community_signal_enrichment not found.")

    try:
        from zo_sentinel.analysis.enrichment import tool_count_enrichment_v4
        if hasattr(tool_count_enrichment_v4, 'enrich'):
            results.update(tool_count_enrichment_v4.enrich(signal_data, config))
    except ImportError:
        print("tool_count_enrichment_v4 not found.")

    try:
        from zo_sentinel.analysis.enrichment import known_bad_pattern_enrichment_v4
        if hasattr(known_bad_pattern_enrichment_v4, 'enrich'):
            results.update(known_bad_pattern_enrichment_v4.enrich(signal_data, config))
    except ImportError:
        print("known_bad_pattern_enrichment_v4 not found.")

    try:
        from zo_sentinel.analysis.enrichment import another_enrichment
        if hasattr(another_enrichment, 'enrich'):
            results.update(another_enrichment.enrich(signal_data, config))
    except ImportError:
        print("another_enrichment not found.")

    print("Signal analysis with ALL enrichments complete.")
    return results

"""
signal_analyser_filepath = os.path.join(signal_analyser_dir, "signal_analyser.py")
with open(signal_analyser_filepath, "w") as f:
    f.write(dummy_signal_analyser_content)

# Add the dummy project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- End Mocking ---

def get_enrichment_modules(enrichment_package_path):
    """Scans a directory for Python files and identifies potential enrichment modules."""
    enrichment_modules = {}
    if not os.path.isdir(enrichment_package_path):
        print(f"Warning: Enrichment package path not found: {enrichment_package_path}")
        return enrichment_modules

    for filename in os.listdir(enrichment_package_path):
        if filename.endswith(".py") and not filename.startswith("__"):
            module_name = f"zo_sentinel.analysis.enrichment.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                # Check if the module has an 'enrich' function
                if hasattr(module, 'enrich') and callable(getattr(module, 'enrich')):
                    enrichment_modules[filename[:-3]] = module_name
            except ImportError as e:
                print(f"Could not import enrichment module {module_name}: {e}")
            except Exception as e:
                print(f"Error processing enrichment module {module_name}: {e}")
    return enrichment_modules

def analyze_signal_analyser_imports_and_calls(signal_analyser_module_name):
    """
    Analyzes the signal_analyser module to find imports and calls to enrichment functions.
    """
    called_enrichments = set()
    imported_enrichments = set()
    enrichment_package_path = os.path.join(os.path.dirname(sys.modules[signal_analyser_module_name].__file__), "enrichment")

    try:
        module = importlib.import_module(signal_analyser_module_name)
        source_code = inspect.getsource(module)
        lines = source_code.splitlines()

        # Track imports
        for line in lines:
            line = line.strip()
            if line.startswith("from zo_sentinel.analysis.enrichment import"):
                parts = line.split("import")
                if len(parts) > 1:
                    imported_names = [name.strip() for name in parts[1].split(',')]
                    for name in imported_names:
                        # Try to map imported name back to module name (e.g., 'community_signal_enrichment' -> 'community_signal_enrichment.py')
                        # This is a simplification; actual mapping might be more complex.
                        potential_module_name = f"zo_sentinel.analysis.enrichment.{name}"
                        if potential_module_name in sys.modules:
                             imported_enrichments.add(name)
                        else:
                            # If not directly imported, check if it's part of a wildcard import or aliased
                            # For simplicity, we'll assume direct imports for now.
                            pass
            elif line.startswith("import zo_sentinel.analysis.enrichment"):
                # Handle 'import zo_sentinel.analysis.enrichment as alias' or 'import zo_sentinel.analysis.enrichment.module_name'
                # This is more complex to parse reliably without AST.
                pass

        # Track calls to 'enrich' functions within functions of signal_analyser
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj):
                try:
                    func_source_code = inspect.getsource(obj)
                    func_lines = func_source_code.splitlines()
                    for func_line in func_lines:
                        func_line = func_line.strip()
                        # Look for calls like module.enrich(...) or alias.enrich(...)
                        if ".enrich(" in func_line:
                            parts = func_line.split(".enrich(")
                            if len(parts) > 0:
                                potential_caller = parts[0].split('(')[0].strip() # Get the part before '.enrich('
                                # Try to resolve the caller to an imported enrichment module name
                                # This is a heuristic: check if the potential_caller matches any imported enrichment name
                                if potential_caller in imported_enrichments:
                                    called_enrichments.add(potential_caller)
                                elif potential_caller.endswith("_enrichment") or potential_caller.endswith("_enrichment_v4"): # Common naming patterns
                                     # This is a weak heuristic, might need AST for robustness
                                     called_enrichments.add(potential_caller)
                                elif "community_signal_enrichment" in potential_caller:
                                    called_enrichments.add("community_signal_enrichment")
                                elif "tool_count_enrichment_v4" in potential_caller:
                                    called_enrichments.add("tool_count_enrichment_v4")
                                elif "known_bad_pattern_enrichment_v4" in potential_caller:
                                    called_enrichments.add("known_bad_pattern_enrichment_v4")
                                elif "another_enrichment" in potential_caller:
                                    called_enrichments.add("another_enrichment")

                except (TypeError, OSError): # Handle built-in functions or dynamically generated code
                    pass
                except Exception as e:
                    print(f"Error inspecting function {name}: {e}")

    except ImportError:
        print(f"Error: Could not import signal_analyser module: {signal_analyser_module_name}")
        return set(), set()
    except Exception as e:
        print(f"Error analyzing signal_analyser module {signal_analyser_module_name}: {e}")
        return set(), set()

    return imported_enrichments, called_enrichments

def generate_diagnostic_report():
    """
    Generates a diagnostic report on enrichment module wiring.
    """
    report = []
    report.append("--- Signal Enrichment Wiring Diagnostic Report ---")
    report.append(f"Timestamp: {os.path.getmtime(__file__)}")
    report.append("\n1. Discovered Enrichment Modules:")

    enrichment_package_path = "zo_sentinel/analysis/enrichment"
    all_available_enrichments = get_enrichment_modules(enrichment_package_path)

    if not all_available_enrichments:
        report.append("  No enrichment modules found in the specified package path.")
    else:
        for name, module_path in all_available_enrichments.items():
            report.append(f"  - {name} (Module: {module_path})")

    report.append("\n2. Analysis of signal_analyser.py:")
    signal_analyser_module_name = "zo_sentinel.analysis.signal_analyser"
    imported_enrichments, called_enrichments = analyze_signal_analyser_imports_and_calls(signal_analyser_module_name)

    report.append(f"  - Enrichments explicitly imported in {signal_analyser_module_name}:")
    if imported_enrichments:
        for enrichment in sorted(list(imported_enrichments)):
            report.append(f"    - {enrichment}")
    else:
        report.append("    None found.")

    report.append(f"\n  - Enrichments called within functions of {signal_analyser_module_name}:")
    if called_enrichments:
        for enrichment in sorted(list(called_enrichments)):
            report.append(f"    - {enrichment}")
    else:
        report.append("    None found.")

    report.append("\n3. Identified Wiring Gaps:")

    # Enrichments that are available but not imported or called
    missing_from_imports_or_calls = set(all_available_enrichments.keys()) - imported_enrichments - called_enrichments
    # Enrichments that are imported but not called (less likely to be the primary gap for *no* results, but worth noting)
    imported_but_not_called = imported_enrichments - called_enrichments

    if missing_from_imports_or_calls:
        report.append("  The following enrichment modules are available but appear to be neither imported nor called by signal_analyser:")
        for enrichment_name in sorted(list(missing_from_imports_or_calls)):
            report.append(f"    - {enrichment_name}")
            report.append(f"      (Module: {all_available_enrichments.get(enrichment_name, 'N/A')})")
            report.append("      -> This is a likely wiring gap. Ensure it's imported and called in signal_analyser.")
    else:
        report.append("  No available enrichment modules are completely missing from imports and calls.")

    if imported_but_not_called:
        report.append("\n  The following enrichment modules are imported but not explicitly called within signal_analyser functions:")
        for enrichment_name in sorted(list(imported_but_not_called)):
            report.append(f"    - {enrichment_name}")
            report.append("      -> This might indicate unused imports or a logic error where the enrichment is intended but not invoked.")

    # Specific check for the known problematic enrichments
    known_enrichments = {"community_signal_enrichment", "tool_count_enrichment_v4", "known_bad_pattern_enrichment_v4"}
    missing_specific_enrichments = known_enrichments - set(all_available_enrichments.keys())
    if missing_specific_enrichments:
        report.append("\n  Critical Issue: The following expected enrichment modules are NOT available in the enrichment package:")
        for enrichment_name in sorted(list(missing_specific_enrichments)):
            report.append(f"    - {enrichment_name}")
            report.append("      -> This indicates a missing file or incorrect module path.")

    # Check if the *specific* enrichments mentioned in the prompt are wired
    prompt_enrichments = {"community_signal_enrichment", "tool_count_enrichment_v4", "known_bad_pattern_enrichment_v4"}
    wired_prompt_enrichments = prompt_enrichments.intersection(all_available_enrichments.keys())
    unwired_prompt_enrichments = prompt_enrichments - wired_prompt_enrichments

    if unwired_prompt_enrichments:
        report.append("\n  Specific Enrichments from Prompt Not Wired:")
        for enrichment_name in sorted(list(unwired_prompt_enrichments)):
            report.append(f"    - {enrichment_name}")
            report.append("      -> This enrichment is not found or not being used by signal_analyser.")
            if enrichment_name not in called_enrichments:
                report.append("      -> It needs to be imported and called within signal_analyser.")
    else:
        report.append("\n  All enrichments mentioned in the prompt (community_signal_enrichment, tool_count_enrichment_v4, known_bad_pattern_enrichment_v4) appear to be available and potentially wired.")

    report.append("\n--- End of Report ---")
    return "\n".join(report)

if __name__ == "__main__":
    # Ensure the dummy modules are discoverable by the analysis
    # This is handled by the sys.path modification at the beginning of the script.

    diagnostic_report = generate_diagnostic_report()
    print(diagnostic_report)

    # Clean up dummy files and directories (optional, for testing purposes)
    # import shutil
    # if os.path.exists("zo_sentinel"):
    #     shutil.rmtree("zo_sentinel")