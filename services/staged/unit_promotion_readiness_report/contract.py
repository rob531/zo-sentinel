"""Unit promotion readiness report contract."""
import json
from pathlib import Path


REQUIRED_FILES = ["router.py", "logic.py", "contract.py", "service.toml", "__init__.py"]


def get_unit_promotion_readiness_report(
    services_dir: Path,
    routers_dir: Path,
    already_built_modules: set[str] | None = None,
) -> dict:
    """Generate promotion readiness report for service units."""
    already_built_modules = already_built_modules or set()
    units = []

    for base_dir in [services_dir, routers_dir]:
        if not base_dir.exists():
            continue
        for item in sorted(base_dir.iterdir()):
            if not item.is_dir():
                continue
            service_name = item.name
            unit_files = list(item.iterdir())
            file_names = {f.name for f in unit_files}
            missing = [f for f in REQUIRED_FILES if f not in file_names]
            complete = len(missing) == 0
            ready = complete and service_name in already_built_modules
            units.append({
                "service_name": service_name,
                "directory": str(item),
                "source": base_dir.name,
                "required_files": REQUIRED_FILES,
                "present_files": sorted(file_names),
                "missing_files": missing,
                "complete": complete,
                "already_built": service_name in already_built_modules,
                "ready": ready,
            })

    return {
        "units": units,
        "summary": {
            "total": len(units),
            "complete": sum(1 for u in units if u["complete"]),
            "incomplete": sum(1 for u in units if not u["complete"]),
            "ready": sum(1 for u in units if u["ready"]),
            "not_ready": sum(1 for u in units if not u["ready"]),
        },
    }


def write_unit_promotion_readiness_report(
    output_path: Path,
    services_dir: Path,
    routers_dir: Path,
    already_built_modules: set[str] | None = None,
) -> dict:
    """Generate and write promotion readiness report to JSON file."""
    report = get_unit_promotion_readiness_report(services_dir, routers_dir, already_built_modules)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        complete_service = tmp / "services" / "test_complete_service"
        complete_service.mkdir(parents=True)
        for fname in REQUIRED_FILES:
            (complete_service / fname).write_text(f"# {fname}")

        incomplete_service = tmp / "services" / "test_incomplete_service"
        incomplete_service.mkdir(parents=True)
        for fname in ["router.py", "logic.py", "service.toml", "__init__.py"]:
            (incomplete_service / fname).write_text(f"# {fname}")

        output = tmp / "shared" / "outputs" / "goose" / "promotion_readiness.json"
        report = write_unit_promotion_readiness_report(
            output,
            tmp / "services",
            tmp / "routers",
            already_built_modules={"test_complete_service"},
        )

        complete_unit = next(u for u in report["units"] if u["service_name"] == "test_complete_service")
        incomplete_unit = next(u for u in report["units"] if u["service_name"] == "test_incomplete_service")

        assert complete_unit["ready"] is True, f"Expected ready=True for complete unit, got {complete_unit['ready']}"
        assert incomplete_unit["ready"] is False, f"Expected ready=False for incomplete unit, got {incomplete_unit['ready']}"
        assert output.exists(), "Output file was not created"

        print("PASS")