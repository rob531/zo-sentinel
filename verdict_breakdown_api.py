import uuid

class VerdictBreakdownAPI:
    def __init__(self, metadata):
        self.server_id = metadata.get('server_id')
        self.verdict = metadata.get('verdict')
        self.signals = metadata.get('signals')

        if not self._validate_server_id():
            raise ValueError("Invalid server_id format. Must be UUIDv4.")

    def _validate_server_id(self):
        try:
            val = uuid.UUID(self.server_id, version=4)
        except ValueError:
            return False
        return str(val) == self.server_id

if __name__ == '__main__':
    metadata = {
        'server_id': '550e8400-e29b-41d4-a716-446655440000',
        'verdict': 'TRUSTED_GENERAL',
        'signals': {'domain_trust': 90, 'tool_description_safety': 85}
    }

    api = VerdictBreakdownAPI(metadata)

    assert api.server_id == '550e8400-e29b-41d4-a716-446655440000'
    assert api.verdict == 'TRUSTED_GENERAL'
    assert api.signals == {'domain_trust': 90, 'tool_description_safety': 85}

    print("PASS")