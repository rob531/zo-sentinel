import json
from typing import Dict, Any

class DataAccessLayer:
    def __init__(self, write_service):
        self.write_service = write_service

    def get_server_scores(self, server_id: str) -> Dict[str, Dict[str, Any]]:
        """Retrieve axis scores for a given server_id.

        Args:
            server_id: The ID of the server to retrieve scores for.

        Returns:
            A dictionary with axis scores in the format:
            {
                "axis1": {"label": str, "p_top": float, "p_critical": float, "p_danger": float},
                ...
            }
        """
        query = f"""
        SELECT axis, label, p_top, p_critical, p_danger
        FROM mcp_llm_axis_scores
        WHERE server_id = '{server_id}'
        """
        result = self.write_service.query(query)

        scores = {}
        for row in result:
            axis = row['axis']
            scores[axis] = {
                'label': row['label'],
                'p_top': row['p_top'],
                'p_critical': row['p_critical'],
                'p_danger': row['p_danger']
            }

        return scores

    def get_overall_risk(self, server_id: str) -> float:
        """Calculate the overall risk score for a given server_id.

        Args:
            server_id: The ID of the server to calculate risk for.

        Returns:
            A float representing the overall risk score.
        """
        scores = self.get_server_scores(server_id)
        if not scores:
            return 0.0

        # Calculate average of p_top values across all axes
        total = sum(axis['p_top'] for axis in scores.values())
        return total / len(scores)

    def get_risk_tier(self, server_id: str) -> str:
        """Determine the risk tier for a given server_id.

        Args:
            server_id: The ID of the server to determine risk tier for.

        Returns:
            A string representing the risk tier (e.g., "Low", "Medium", "High").
        """
        overall_risk = self.get_overall_risk(server_id)

        if overall_risk < 0.3:
            return "Low"
        elif overall_risk < 0.7:
            return "Medium"
        else:
            return "High"

    def apply_trust_gating(self, server_id: str, scores: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Apply trust gating overrides to the scores.

        Args:
            server_id: The ID of the server.
            scores: The scores to apply overrides to.

        Returns:
            The scores with overrides applied.
        """
        # Mock implementation - replace with actual trust_gating_override.trust_gate() call
        overrides = {
            "server1": {"axis1": {"p_top": 0.9, "p_critical": 0.8, "p_danger": 0.7}},
            "server2": {"axis2": {"p_top": 0.1, "p_critical": 0.2, "p_danger": 0.3}}
        }

        if server_id in overrides:
            for axis, override in overrides[server_id].items():
                if axis in scores:
                    scores[axis].update(override)

        return scores

def main():
    # Mock write_service for testing
    class MockWriteService:
        def query(self, query):
            # Mock data for testing
            if "server1" in query:
                return [
                    {"axis": "axis1", "label": "Label 1", "p_top": 0.8, "p_critical": 0.7, "p_danger": 0.6},
                    {"axis": "axis2", "label": "Label 2", "p_top": 0.7, "p_critical": 0.6, "p_danger": 0.5},
                    {"axis": "axis3", "label": "Label 3", "p_top": 0.6, "p_critical": 0.5, "p_danger": 0.4},
                    {"axis": "axis4", "label": "Label 4", "p_top": 0.5, "p_critical": 0.4, "p_danger": 0.3},
                    {"axis": "axis5", "label": "Label 5", "p_top": 0.4, "p_critical": 0.3, "p_danger": 0.2},
                    {"axis": "axis6", "label": "Label 6", "p_top": 0.3, "p_critical": 0.2, "p_danger": 0.1},
                    {"axis": "axis7", "label": "Label 7", "p_top": 0.2, "p_critical": 0.1, "p_danger": 0.0}
                ]
            else:
                return []

    write_service = MockWriteService()
    dal = DataAccessLayer(write_service)

    # Test with sample server_id
    server_id = "server1"
    scores = dal.get_server_scores(server_id)
    overall_risk = dal.get_overall_risk(server_id)
    risk_tier = dal.get_risk_tier(server_id)

    # Apply trust gating
    scores_with_overrides = dal.apply_trust_gating(server_id, scores)

    # Assert returned shape has 7 axes with p_top values
    assert len(scores) == 7
    for axis in scores.values():
        assert "p_top" in axis

    print("PASS")

if __name__ == "__main__":
    main()