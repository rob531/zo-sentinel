"""
Breaker action to investigate mcp_definition_history emptiness.
"""

import logging

logger = logging.getLogger(__name__)

def run():
    """
    Entry point for the breaker action.

    This function does not attempt to rebuild the table. Instead it logs a warning
    and raises a BreakerInvestigationRequired exception which can be caught by
    the surrounding breaker framework to start the appropriate investigation workflow.
    """
    logger.warning("Breaker action 'investigate_mcp_definition_history' triggered: "
                   "mcp_definition_history table is empty.")
    # Raise a specific exception to signal investigation needed
    raise InvestigationRequired(
        "mcp_definition_history table is empty; manual investigation required."
    )

class InvestigationRequired(RuntimeError):
    """Exception indicating that a manual investigation is required."""
    pass