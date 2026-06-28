from zo_sentinel.breaker_actions import BreakerAction
from zo_sentinel.breaker_workflows import BreakerWorkflow

class BreakerActionInvestigateWisdomSynthesiser(BreakerAction):
    """
    Quality-gate breaker action 'investigate' for wisdom_synthesiser.
    Rationale: The wisdom_synthesiser daemon is stale. Understanding the reason for its staleness is important for overall system health.
    This directive does NOT rebuild wisdom_synthesiser; it triggers a breaker workflow.
    Proposed by directive_architect at 2026-06-27T13:07:54.741526+00:00.
    """

    def __init__(self):
        super().__init__(
            name="investigate_wisdom_synthesiser",
            description="Investigate the staleness of the wisdom_synthesiser daemon.",
            target="wisdom_synthesiser",
            rationale="The wisdom_synthesiser daemon is stale. Understanding the reason for its staleness is important for overall system health.",
            proposed_by="directive_architect",
            proposed_at="2026-06-27T13:07:54.741526+00:00"
        )

    def execute(self, context):
        """
        Execute the breaker action.
        """
        # Trigger the breaker workflow for investigating the wisdom_synthesiser daemon
        workflow = BreakerWorkflow(
            name="investigate_wisdom_synthesiser_workflow",
            description="Investigate the staleness of the wisdom_synthesiser daemon.",
            target="wisdom_synthesiser",
            rationale="The wisdom_synthesiser daemon is stale. Understanding the reason for its staleness is important for overall system health.",
            proposed_by="directive_architect",
            proposed_at="2026-06-27T13:07:54.741526+00:00"
        )
        workflow.execute(context)