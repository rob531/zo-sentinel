from datetime import datetime, timedelta
from typing import List, Dict
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy import func, and_
import requests
from unittest.mock import patch

def compute_overall_risk_trend(server_id: str, days: int = 30) -> List[Dict]:
    session = get_session()
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    results = session.query(
        func.date(MCPLLMAxisScores.scored_at).label('date'),
        func.avg(MCPLLMAxisScores.p_top).label('overall_risk')
    ).filter(
        and_(
            MCPLLMAxisScores.server_id == server_id,
            MCPLLMAxisScores.axis_name == 'overall_risk',
            MCPLLMAxisScores.scored_at >= start_date,
            MCPLLMAxisScores.scored_at <= end_date
        )
    ).group_by(
        func.date(MCPLLMAxisScores.scored_at)
    ).order_by(
        func.date(MCPLLMAxisScores.scored_at)
    ).all()

    session.close()

    return [
        {'date': result.date.isoformat(), 'overall_risk': float(result.overall_risk)}
        for result in results
    ]

if __name__ == '__main__':
    mock_response = {
        "rows": [
            {"server_id": "srv-123", "axis_name": "overall_risk", "p_top": 0.8, "scored_at": "2023-01-01T00:00:00"},
            {"server_id": "srv-123", "axis_name": "overall_risk", "p_top": 0.7, "scored_at": "2023-01-02T00:00:00"},
            {"server_id": "srv-123", "axis_name": "overall_risk", "p_top": 0.9, "scored_at": "2023-01-03T00:00:00"},
        ]
    }

    with patch('requests.post') as mock_post:
        mock_post.return_value.json.return_value = mock_response

        result = compute_overall_risk_trend('srv-123', 3)

        assert len(result) == 3
        assert all(isinstance(item['date'], str) for item in result)
        print('PASS')