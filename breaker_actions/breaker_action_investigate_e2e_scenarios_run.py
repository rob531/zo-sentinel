import os
import sys

# Add the root of the project to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

from zo_sentinel.breaker_actions.breaker_action import BreakerAction
from zo_sentinel.breaker_actions.breaker_action_utils import (
    get_file_path,
    get_commit_hash,
    get_repo_url,
)


class InvestigateE2eScenariosRunPy(BreakerAction):
    """
    Breaker action to investigate failures in e2e_scenarios_run.py.

    Rationale: Quarantined file with recent failures, needs investigation
    before any rebuild attempts.
    """

    def __init__(self):
        super().__init__(
            name="investigate_e2e_scenarios_run_py",
            description="Investigate e2e_scenarios_run.py due to recent failures.",
            target_file="breaker_actions/breaker_action_investigate_e2e_scenarios_run.py",
            rationale="Quarantined file with recent failures, needs investigation before any rebuild attempts.",
            proposed_by="directive_architect",
            proposed_at="2026-06-27T17:38:17.091535+00:00",
        )

    def trigger(self) -> bool:
        """
        Triggers the investigation workflow.

        This action does not automatically rebuild the file. It's designed
        to halt the build process and signal that manual investigation is required.
        """
        print(
            f"Breaker action '{self.name}' triggered for file: {self.target_file}"
        )
        print(f"Rationale: {self.rationale}")
        print(
            f"Proposed by: {self.proposed_by} at {self.proposed_at}"
        )

        # In a real-world scenario, this would involve:
        # 1. Creating a bug ticket or incident.
        # 2. Notifying the relevant team.
        # 3. Potentially quarantining the file or related tests.
        # 4. Setting a flag to prevent automatic rebuilds until investigation is complete.

        # For this example, we'll just print a message indicating the investigation
        # workflow has been initiated.
        print(
            "Initiating investigation workflow. Manual review of recent failures "
            "in e2e_scenarios_run.py is required. Automatic rebuilds are paused."
        )

        # This action does NOT rebuild the file. It signals a need for investigation.
        return False  # Return False to indicate that the build should not proceed


if __name__ == "__main__":
    # Example usage:
    action = InvestigateE2eScenariosRunPy()

    # Simulate a condition where this action should be triggered
    # In a real system, this would be determined by build system logic
    # based on failure analysis.
    should_trigger = True

    if should_trigger:
        action.trigger()
    else:
        print("Breaker action not triggered.")

    # Example of how to get file path and commit hash (for context)
    file_path = get_file_path(__file__)
    commit_hash = get_commit_hash(file_path)
    repo_url = get_repo_url(file_path)

    print(f"\n--- Context Information ---")
    print(f"Current file path: {file_path}")
    print(f"Current commit hash: {commit_hash}")
    print(f"Repository URL: {repo_url}")