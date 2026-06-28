import logging
import os
import subprocess
from typing import Dict, List, Optional

from breaker_actions.breaker_action import BreakerAction
from breaker_actions.breaker_action_status import BreakerActionStatus
from breaker_actions.breaker_action_type import BreakerActionType
from breaker_actions.breaker_workflow import BreakerWorkflow
from common.utils import get_file_path, run_command
from config import QUARANTINE_DIR, SOURCE_DIR

class InvestigateVerifyWriteServiceDataWriteFunctionality(BreakerAction):
    """Breaker action to investigate the root cause of failures in verify_write_service_data_write_functionality.py."""

    def __init__(self):
        super().__init__(
            name="investigate_verify_write_service_data_write_functionality",
            description="Investigate the root cause of failures in verify_write_service_data_write_functionality.py",
            action_type=BreakerActionType.INVESTIGATE,
            target_file="verify_write_service_data_write_functionality.py",
            quarantine_dir=QUARANTINE_DIR,
            source_dir=SOURCE_DIR,
        )

    def execute(self) -> BreakerActionStatus:
        """Execute the investigation workflow."""
        logging.info(f"Starting investigation for {self.target_file}")

        # Step 1: Check if the file exists in quarantine
        quarantined_file_path = get_file_path(self.quarantine_dir, self.target_file)
        if not os.path.exists(quarantined_file_path):
            logging.error(f"File {self.target_file} not found in quarantine directory")
            return BreakerActionStatus.FAILED

        # Step 2: Run static analysis tools
        static_analysis_results = self._run_static_analysis(quarantined_file_path)
        if not static_analysis_results:
            logging.error("Static analysis failed")
            return BreakerActionStatus.FAILED

        # Step 3: Run unit tests in isolation
        test_results = self._run_unit_tests(quarantined_file_path)
        if not test_results:
            logging.error("Unit tests failed")
            return BreakerActionStatus.FAILED

        # Step 4: Analyze logs and test results
        analysis_results = self._analyze_results(static_analysis_results, test_results)
        if not analysis_results:
            logging.error("Analysis failed")
            return BreakerActionStatus.FAILED

        # Step 5: Generate report
        report = self._generate_report(analysis_results)
        if not report:
            logging.error("Report generation failed")
            return BreakerActionStatus.FAILED

        logging.info(f"Investigation completed for {self.target_file}")
        return BreakerActionStatus.SUCCESS

    def _run_static_analysis(self, file_path: str) -> Optional[Dict]:
        """Run static analysis tools on the file."""
        logging.info(f"Running static analysis on {file_path}")

        # Example: Run pylint
        pylint_cmd = ["pylint", file_path]
        pylint_result = run_command(pylint_cmd)

        if pylint_result.returncode != 0:
            logging.error(f"Pylint failed with return code {pylint_result.returncode}")
            return None

        return {"pylint": pylint_result.stdout}

    def _run_unit_tests(self, file_path: str) -> Optional[Dict]:
        """Run unit tests for the file in isolation."""
        logging.info(f"Running unit tests for {file_path}")

        # Example: Run pytest
        pytest_cmd = ["pytest", file_path]
        pytest_result = run_command(pytest_cmd)

        if pytest_result.returncode != 0:
            logging.error(f"Pytest failed with return code {pytest_result.returncode}")
            return None

        return {"pytest": pytest_result.stdout}

    def _analyze_results(self, static_analysis_results: Dict, test_results: Dict) -> Optional[Dict]:
        """Analyze the results from static analysis and unit tests."""
        logging.info("Analyzing results")

        analysis = {
            "static_analysis": static_analysis_results,
            "test_results": test_results,
            "issues": []
        }

        # Example analysis: Check for common issues
        if "pylint" in static_analysis_results:
            pylint_output = static_analysis_results["pylint"]
            if "error" in pylint_output.lower():
                analysis["issues"].append("Pylint found errors")

        if "pytest" in test_results:
            pytest_output = test_results["pytest"]
            if "fail" in pytest_output.lower():
                analysis["issues"].append("Pytest found failures")

        return analysis

    def _generate_report(self, analysis_results: Dict) -> Optional[str]:
        """Generate a report based on the analysis results."""
        logging.info("Generating report")

        report = f"Investigation Report for {self.target_file}\n"
        report += "=" * 50 + "\n\n"

        report += "Static Analysis Results:\n"
        report += str(analysis_results.get("static_analysis", {})) + "\n\n"

        report += "Test Results:\n"
        report += str(analysis_results.get("test_results", {})) + "\n\n"

        report += "Identified Issues:\n"
        report += "\n".join(analysis_results.get("issues", [])) + "\n\n"

        report += "Recommendations:\n"
        report += "1. Fix the identified issues.\n"
        report += "2. Re-run the tests to verify the fixes.\n"

        report_path = os.path.join(self.quarantine_dir, f"investigation_report_{self.target_file}.txt")
        with open(report_path, "w") as f:
            f.write(report)

        logging.info(f"Report generated at {report_path}")
        return report_path

if __name__ == "__main__":
    action = InvestigateVerifyWriteServiceDataWriteFunctionality()
    status = action.execute()

    if status == BreakerActionStatus.SUCCESS:
        logging.info("Investigation completed successfully")
    else:
        logging.error("Investigation failed")