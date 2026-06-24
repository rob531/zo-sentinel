import uuid

class ProductAuditLog:
    def __init__(self, metadata: dict):
        self.user_id = metadata.get('user_id')
        self.action = metadata.get('action')
        self.timestamp = metadata.get('timestamp')
        
        if not self._validate_user_id(self.user_id):
            raise ValueError("Invalid user_id format. Must be UUIDv4.")

    @staticmethod
    def _validate_user_id(user_id: str) -> bool:
        try:
            return uuid.UUID(user_id, version=4).hex == user_id.replace('-', '')
        except (ValueError, AttributeError):
            return False

    def __repr__(self):
        return f"ProductAuditLog(user_id='{self.user_id}', action='{self.action}', timestamp='{self.timestamp}')"

if __name__ == '__main__':
    # Self-test
    test_metadata = {
        'user_id': '550e8400-e29b-41d4-a716-446655440000',
        'action': 'login',
        'timestamp': '2026-06-24T14:00:00'
    }
    
    try:
        audit_log = ProductAuditLog(test_metadata)
        assert audit_log.user_id == test_metadata['user_id']
        assert audit_log.action == test_metadata['action']
        assert audit_log.timestamp == test_metadata['timestamp']
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
