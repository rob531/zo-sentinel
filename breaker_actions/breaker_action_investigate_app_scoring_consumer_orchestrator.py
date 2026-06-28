from zo_sentinel.breaker_actions import BreakerAction
from zo_sentinel.breaker_workflows import InvestigateWorkflow

class InvestigateAppScoringConsumerOrchestrator(BreakerAction):
    """
    Quality-gate breaker action 'investigate' for app_scoring_consumer_orchestrator.py.
    Rationale: Quarantined file with recent failures, needs investigation before any rebuild attempts.
    This directive does NOT rebuild app_scoring_consumer_orchestrator.py; it triggers a breaker workflow.
    """

    def __init__(self):
        super().__init__(
            name="investigate_app_scoring_consumer_orchestrator",
            description="Investigate app_scoring_consumer_orchestrator.py due to recent failures",
            target_file="app_scoring_consumer_orchestrator.py",
            workflow=InvestigateWorkflow(),
            rationale="Quarantined file with recent failures, needs investigation before any rebuild attempts."
        )

    def execute(self, context):
        """
        Execute the breaker action.
        """
        self.workflow.execute(context)