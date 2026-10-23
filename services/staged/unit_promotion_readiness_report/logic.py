"""
Unit Promotion Readiness Report Generator

This service evaluates which built services are promotion-ready by scanning
the filesystem for service units and checking their scaffold completeness.
"""

import json
import os
from pathlib import Path
from typing import Any

# Required files for a complete service unit scaffold
REQUIRED_FILES = ["router.py", "logic.py", "contract.py", "service.toml", "__init__.py"]


def get_service_units(base_path: Path) -> list[dict[str, Any]]:
    """
    Enumerate all service unit directories under the given base path.
    
    Args:
        base_path: Root directory to scan for service units
        
    Returns:
        List of service unit dictionaries with path and name
    """
    service_units = []
    
    if not base_path.exists():
        return service_units
    
    for item in sorted(base_path.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            service_units.append({
                "name": item.name,
                "path": str(item)
            })
    
    return service_units


def check_scaffold_completeness(service_path: Path) -> dict[str, Any]:
    """
    Check if a service unit has all required scaffold files.
    
    Args:
        service_path: Path to the service unit directory
        
    Returns:
        Dictionary with completeness status and missing files list
    """
    missing_files = []
    present_files = []
    
    for required_file in REQUIRED_FILES:
        file_path = service_path / required_file
        if file_path.exists():
            present_files.append(required_file)
        else:
            missing_files.append(required_file)
    
    is_complete = len(missing_files) == 0
    
    return {
        "is_complete": is_complete,
        "missing_files": missing_files,
        "present_files": present_files
    }


def get_already_built_modules() -> set[str]:
    """
    Get the set of already built modules from the shared state.
    This reads from shared/outputs/goose/build_status.json if it exists.
    
    Returns:
        Set of module names that are already built
    """
    build_status_path = Path("shared/outputs/goose/build_status.json")
    
    if not build_status_path.exists():
        return set()
    
    try:
        with open(build_status_path, "r") as f:
            build_status = json.load(f)
            return set(build_status.get("built_modules", []))
    except (json.JSONDecodeError, IOError):
        return set()


def generate_readiness_matrix(
    services_base: Path,
    routers_base: Path,
    already_built: set[str]
) -> dict[str, Any]:
    """
    Generate a readiness matrix for all service units.
    
    Args:
        services_base: Base path for services directory
        routers_base: Base path for routers directory
        already_built: Set of already built module names
        
    Returns:
        Readiness matrix dictionary
    """
    matrix = {
        "services": [],
        "summary": {
            "total_services": 0,
            "ready_count": 0,
            "not_ready_count": 0,
            "already_built_count": 0
        }
    }
    
    # Scan services directory
    for service_unit in get_service_units(services_base):
        service_path = Path(service_unit["path"])
        scaffold_check = check_scaffold_completeness(service_path)
        
        is_ready = scaffold_check["is_complete"]
        is_built = service_unit["name"] in already_built
        
        service_entry = {
            "name": service_unit["name"],
            "source": "services",
            "path": service_unit["path"],
            "ready": is_ready,
            "scaffold_complete": scaffold_check["is_complete"],
            "missing_files": scaffold_check["missing_files"],
            "present_files": scaffold_check["present_files"],
            "already_built": is_built,
            "promotion_ready": is_ready and not is_built
        }
        
        matrix["services"].append(service_entry)
        matrix["summary"]["total_services"] += 1
        
        if is_ready:
            matrix["summary"]["ready_count"] += 1
        else:
            matrix["summary"]["not_ready_count"] += 1
        
        if is_built:
            matrix["summary"]["already_built_count"] += 1
    
    # Scan routers directory
    for service_unit in get_service_units(routers_base):
        service_path = Path(service_unit["path"])
        scaffold_check = check_scaffold_completeness(service_path)
        
        is_ready = scaffold_check["is_complete"]
        is_built = service_unit["name"] in already_built
        
        service_entry = {
            "name": service_unit["name"],
            "source": "routers",
            "path": service_unit["path"],
            "ready": is_ready,
            "scaffold_complete": scaffold_check["is_complete"],
            "missing_files": scaffold_check["missing_files"],
            "present_files": scaffold_check["present_files"],
            "already_built": is_built,
            "promotion_ready": is_ready and not is_built
        }
        
        matrix["services"].append(service_entry)
        matrix["summary"]["total_services"] += 1
        
        if is_ready:
            matrix["summary"]["ready_count"] += 1
        else:
            matrix["summary"]["not_ready_count"] += 1
        
        if is_built:
            matrix["summary"]["already_built_count"] += 1
    
    return matrix


def write_readiness_report(matrix: dict[str, Any], output_path: Path) -> None:
    """
    Write the readiness matrix to a JSON file.
    
    Args:
        matrix: Readiness matrix dictionary
        output_path: Path to write the JSON output
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(matrix, f, indent=2)


def run() -> dict[str, Any]:
    """
    Main execution function for the unit promotion readiness report.
    
    Returns:
        The readiness matrix dictionary
    """
    services_base = Path("app/services")
    routers_base = Path("app/routers")
    output_path = Path("shared/outputs/goose/promotion_readiness.json")
    
    already_built = get_already_built_modules()
    matrix = generate_readiness_matrix(services_base, routers_base, already_built)
    write_readiness_report(matrix, output_path)
    
    return matrix


if __name__ == "__main__":
    import tempfile
    import shutil
    
    # Create a temporary directory for testing
    test_dir = Path(tempfile.mkdtemp())
    
    try:
        # Create complete service unit
        complete_service = test_dir / "complete_service"
        complete_service.mkdir()
        for filename in REQUIRED_FILES:
            (complete_service / filename).touch()
        
        # Create incomplete service unit (missing contract.py)
        incomplete_service = test_dir / "incomplete_service"
        incomplete_service.mkdir()
        for filename in REQUIRED_FILES:
            if filename != "contract.py":
                (incomplete_service / filename).touch()
        
        # Create another incomplete service (missing multiple files)
        missing_multiple = test_dir / "missing_multiple"
        missing_multiple.mkdir()
        (missing_multiple / "router.py").touch()
        (missing_multiple / "logic.py").touch()
        
        # Scan the test directory
        service_units = get_service_units(test_dir)
        
        # Verify results
        complete_check = check_scaffold_completeness(complete_service)
        incomplete_check = check_scaffold_completeness(incomplete_service)
        missing_multiple_check = check_scaffold_completeness(missing_multiple)
        
        # Generate matrix
        already_built = set()
        matrix = generate_readiness_matrix(test_dir, test_dir, already_built)
        
        # Find our test services in the matrix
        complete_entry = next((s for s in matrix["services"] if s["name"] == "complete_service"), None)
        incomplete_entry = next((s for s in matrix["services"] if s["name"] == "incomplete_service"), None)
        missing_multiple_entry = next((s for s in matrix["services"] if s["name"] == "missing_multiple"), None)
        
        # Run assertions
        assert complete_entry is not None, "Complete service not found in matrix"
        assert complete_entry["ready"] is True, f"Complete service should be ready=True, got {complete_entry['ready']}"
        assert complete_entry["scaffold_complete"] is True, "Complete service scaffold should be complete"
        assert len(complete_entry["missing_files"]) == 0, "Complete service should have no missing files"
        
        assert incomplete_entry is not None, "Incomplete service not found in matrix"
        assert incomplete_entry["ready"] is False, f"Incomplete service should be ready=False, got {incomplete_entry['ready']}"
        assert incomplete_entry["scaffold_complete"] is False, "Incomplete service scaffold should not be complete"
        assert "contract.py" in incomplete_entry["missing_files"], "contract.py should be in missing files"
        
        assert missing_multiple_entry is not None, "Missing multiple service not found in matrix"
        assert missing_multiple_entry["ready"] is False, "Missing multiple service should not be ready"
        assert len(missing_multiple_entry["missing_files"]) == 3, "Should have 3 missing files"
        
        # Test with already_built set
        matrix_with_built = generate_readiness_matrix(test_dir, test_dir, {"complete_service"})
        complete_with_built = next((s for s in matrix_with_built["services"] if s["name"] == "complete_service"), None)
        assert complete_with_built["already_built"] is True, "complete_service should be marked as already built"
        assert complete_with_built["promotion_ready"] is False, "already built service should not be promotion_ready"
        
        # Verify write functionality
        output_path = test_dir / "promotion_readiness.json"
        write_readiness_report(matrix, output_path)
        assert output_path.exists(), "Output file should be created"
        
        with open(output_path, "r") as f:
            loaded = json.load(f)
            assert "services" in loaded, "Output should have services key"
            assert "summary" in loaded, "Output should have summary key"
        
        print("PASS")
        
    finally:
        # Clean up
        shutil.rmtree(test_dir)