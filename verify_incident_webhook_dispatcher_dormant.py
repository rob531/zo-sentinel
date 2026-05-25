"""
ZO-SENTINEL: verify_incident_webhook_dispatcher_dormant.py
Diagnostic verification module confirming incident_webhook_dispatcher.py remains dormant.
Per spec section 9: outbound webhooks are out of scope.
"""
import ast
import os
import sys
from pathlib import Path
from typing import Any


class DormancyVerifier:
    """Verifies incident_webhook_dispatcher.py is properly isolated."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.results: dict[str, Any] = {
            "file_exists": False,
            "imported_by_active_daemon": False,
            "mounted_in_http_routes": False,
            "triggered_by_write_service": False,
            "details": [],
            "overall_status": "PASS"
        }

    def check_file_exists(self) -> bool:
        """Verify the dormant file exists but is not active."""
        target_path = self.project_root / "incident_webhook_dispatcher.py"
        exists = target_path.exists()
        self.results["file_exists"] = exists
        self.results["details"].append(
            f"File exists: {exists} ({target_path})"
        )
        return exists

    def _scan_python_files(self) -> list[Path]:
        """Get all Python files in project."""
        py_files = []
        for pattern in ["*.py", "*.pyi"]:
            py_files.extend(self.project_root.rglob(pattern))
        return py_files

    def _is_active_daemon(self, path: Path) -> bool:
        """Check if Python file is an active daemon/service."""
        name = path.stem.lower()
        active_patterns = [
            "daemon", "server", "service", "worker", "main"
        ]
        return any(p in name for p in active_patterns)

    def check_imports(self) -> None:
        """Scan all active daemon files for imports of dormant module."""
        py_files = self._scan_python_files()
        dormant_imports = ["incident_webhook_dispatcher"]

        for py_file in py_files:
            if not self._is_active_daemon(py_file):
                continue

            try:
                content = py_file.read_text()
                tree = ast.parse(content, filename=str(py_file))
            except (SyntaxError, OSError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(d in alias.name for d in dormant_imports):
                            self.results["imported_by_active_daemon"] = True
                            self.results["details"].append(
                                f"ACTIVE IMPORT: {py_file} imports {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and any(
                        d in node.module for d in dormant_imports
                    ):
                        self.results["imported_by_active_daemon"] = True
                        self.results["details"].append(
                            f"ACTIVE IMPORT: {py_file} imports from {node.module}"
                        )

    def check_http_routes(self) -> None:
        """Scan for HTTP routes that mount the dormant module."""
        py_files = self._scan_python_files()

        for py_file in py_files:
            try:
                content = py_file.read_text()
            except OSError:
                continue

            if "incident_webhook_dispatcher" in content:
                if "router" in content.lower() or "app" in content.lower() or "route" in content.lower():
                    if any(
                        f"include_router({d}" in content.replace(" ", "")
                        for d in ["incident_webhook_dispatcher"]
                    ):
                        self.results["mounted_in_http_routes"] = True
                        self.results["details"].append(
                            f"HTTP ROUTE: {py_file} mounts incident_webhook_dispatcher"
                        )

    def check_write_service_calls(self) -> None:
        """Check write_service calls for triggers to dormant module."""
        py_files = self._scan_python_files()

        for py_file in py_files:
            try:
                content = py_file.read_text()
            except OSError:
                continue

            if "write_service" in content or "requests.post" in content:
                if "incident_webhook" in content.lower():
                    self.results["triggered_by_write_service"] = True
                    self.results["details"].append(
                        f"WRITE_SERVICE: {py_file} calls write_service for incident_webhook"
                    )

    def verify(self) -> dict[str, Any]:
        """Run all verification checks."""
        self.check_file_exists()

        if not self.results["file_exists"]:
            self.results["details"].append(
                "WARNING: incident_webhook_dispatcher.py not found in project"
            )
            return self.results

        self.check_imports()
        self.check_http_routes()
        self.check_write_service_calls()

        is_dormant = not any([
            self.results["imported_by_active_daemon"],
            self.results["mounted_in_http_routes"],
            self.results["triggered_by_write_service"]
        ])

        self.results["overall_status"] = "PASS" if is_dormant else "FAIL"
        self.results["is_properly_isolated"] = is_dormant

        return self.results


def run() -> dict[str, Any]:
    """Main entry point for verification."""
    project_root = Path("/home/workspace/zo_sentinel")

    if not project_root.exists():
        return {
            "error": f"Project root not found: {project_root}",
            "overall_status": "ERROR"
        }

    verifier = DormancyVerifier(project_root)
    results = verifier.verify()

    status = results["overall_status"]
    print(f"\n{'='*60}")
    print(f"ZO-SENTINEL: Dormancy Verification Report")
    print(f"{'='*60}")
    print(f"Status: {status}")
    print(f"File Exists: {results['file_exists']}")
    print(f"Imported by Active Daemon: {results['imported_by_active_daemon']}")
    print(f"Mounted in HTTP Routes: {results['mounted_in_http_routes']}")
    print(f"Triggered by write_service: {results['triggered_by_write_service']}")
    print(f"\nDetails:")
    for detail in results["details"]:
        print(f"  - {detail}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    run()