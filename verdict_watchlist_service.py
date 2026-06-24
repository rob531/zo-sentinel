import uuid

class VerdictWatchlistService:
    def __init__(self, metadata):
        self.server_id = metadata.get('server_id')
        self.verdict = metadata.get('verdict')
        self.watchlist_reason = metadata.get('watchlist_reason')

        if not self._is_valid_uuid(self.server_id):
            raise ValueError("Invalid server_id format. Must be a valid UUIDv4.")

    def _is_valid_uuid(self, val):
        try:
            return str(uuid.UUID(val, version=4)) == val
        except ValueError:
            return False

if __name__ == '__main__':
    test_metadata = {
        'server_id': '550e8400-e29b-41d4-a716-446655440000',
        'verdict': 'HIGH_RISK_ISOLATED',
        'watchlist_reason': 'Suspicious activity'
    }

    service = VerdictWatchlistService(test_metadata)

    assert service.server_id == test_metadata['server_id']
    assert service.verdict == test_metadata['verdict']
    assert service.watchlist_reason == test_metadata['watchlist_reason']

    print("PASS")