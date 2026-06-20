import logging
from typing import Dict, Any
from zo_sentinel.breaker_actions.breaker_action import BreakerAction
from zo_sentinel.workflow import Workflow
from zo_sentinel.quality_gate import QualityGate

class ResetQualityGateAction(BreakerAction):
    """Breaker action to reset a tripped quality gate."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = logging.getLogger(__name__)
        self.quality_gate = QualityGate(config['quality_gate_config'])

    def execute(self) -> bool:
        """Execute the reset action on the quality gate."""
        try:
            self.logger.info("Resetting tripped quality gate...")
            self.quality_gate.reset()
            self.logger.info("Quality gate reset successful.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to reset quality gate: {str(e)}")
            return False

    def get_workflow(self) -> Workflow:
        """Get the workflow associated with this breaker action."""
        return Workflow(
            name="quality_gate_reset_workflow",
            steps=[
                {
                    "name": "reset_quality_gate",
                    "action": self.execute,
                    "description": "Reset the tripped quality gate to unblock build directives."
                }
            ]
        )

# Example usage (not part of the actual implementation)
if __name__ == "__main__":
    config = {
        'quality_gate_config': {
            'name': 'main_quality_gate',
            'threshold': 0.95,
            'metrics': ['coverage', 'test_pass_rate']
        }
    }
    action = ResetQualityGateAction(config)
    action.execute()