import requests
from typing import Dict, List
import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class DiagnosticModule:
    def __init__(self):
        self.write_service_url = "http://127.0.0.1:8772"
        self.signal_types = ["permission_scope"]

    async def ws_query(self, start_date, end_date):
        params = {
            'start_date': start_date,
            'end_date': end_date
        }
        headers = {'Content-Type': 'application/json'}
        data = {
            "table": "mcp_signal_scores",
            "rows": {
                "signal_type": self.signal_types[0],
                "status": ["success", "failure"]
            },
            "wait": True
        }
        response = requests.post(f"{self.write_service_url}/write", json=data, headers=headers)
        return response

    async def get_signal_distribution(self):
        start_date = (datetime.now() - datetime.timedelta(days=30)).isoformat()
        end_date = (datetime.now()).isoformat()
        async with self.ws_query(start_date, end_date) as response:
            signal_scores = json.loads(response.text)['rows']['results']
        if not signal_scores:
            return {
                'signal_distribution': {},
                'top_contributing_metadata_fields': [],
                'recommendations': []
            }
        scores = [score['value'] for score in signal_scores]
        distribution = {}
        top_contributing_fields = []
        recommendations = []
        for i, score in enumerate(set(scores)):
            count = scores.count(score)
            if count > 1:
                top_contributing_fields.append(f"Field {i+1}")
                recommendations.append(f"Ensure field {i+1} is consistent")
            distribution[score] = count
        return {
            'signal_distribution': distribution,
            'top_contributing_metadata_fields': top_contributing_fields,
            'recommendations': recommendations
        }

    async def diagnose_permission_scope_weak_signal(self):
        report = await self.get_signal_distribution()
        if not report['signal_distribution']:
            return {
                'diagnostic_report': "No data available",
                'top_contributing_metadata_fields': [],
                'recommendations': []
            }
        return report

async def main():
    diagnostic_module = DiagnosticModule()
    result = await diagnostic_module.diagnose_permission_scope_weak_signal()
    print(json.dumps(result, indent=4))

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())