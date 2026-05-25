import requests
from datetime import datetime, timedelta
from typing import Dict, List

class DiagnoseWisdomSynthesiser:
    def __init__(self):
        self.base_url = 'http://127.0.0.1:8772/write'
        self.threshold = 14400

    def diagnose(self) -> Dict[str, str]:
        now = datetime.now()
        last_heartbeat = (now - timedelta(seconds=self.threshold)).strftime('%Y-%m-%d %H:%M:%S')
        
        query_params = {'table': 'wisdom_synthesiser_health', 'rows': [{'service': 'name', 'last_heartbeat': last_heartbeat}]}

        response = requests.post(self.base_url, json=query_params)

        if response.status_code == 200:
            return response.json()
        else:
            error = {
                "error": "Failed to diagnose wisdom_synthesiser staleness",
                "code": str(response.status_code),
                "message": response.text
            }
            return error

def run():
    diagnose_wisdom_synthesiser = DiagnoseWisdomSynthesiser()
    diagnostics = diagnose_wisdom_synthesiser.diagnose()

    print(diagnostics)

if __name__ == '__main__':
    import logging
    from logging.handlers import RotatingFileHandler

    logger = logging.getLogger('zo_sentinel')
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler('/var/log/zo_sentinel diagnosed_wisdom_synthesiser.log', maxBytes=1024*1024*100, backupCount=10)
    logger.addHandler(file_handler)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    logger.info('Starting diagnosed_wisdom_synthesiser')

    run()