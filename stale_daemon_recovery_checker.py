import requests

class ServiceHealthChecker:
    def __init__(self, host='http://127.0.0.1'):
        self.host = host

    async def query_service_health(self):
        data = {'table': 'service_health', 'rows': {'service': 'all_daemons', 'last_heartbeat': None}}
        response = requests.post(f'{self.host}/write', json=data)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception('Failed to query service health')

    async def get_stale_daemons(self):
        try:
            data = await self.query_service_health()
        except Exception as e:
            print(e)
            return None
        stale_daemons = {}
        for row in data['rows']['all_daemons']:
            if 'sta' not in row or row['sta'] is False:
                stale_daemons[row['service']] = (row.get('last_heartbeat'))
        return stale_daemons

async def main():
    checker = ServiceHealthChecker()
    stale_daemons = await checker.get_stale_daemons()

def run():
    if __name__ == '__main__':
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())

if __name__=='__main__':
    run()