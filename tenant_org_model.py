import re
from datetime import datetime

class TenantOrgModel:
    def __init__(self, metadata):
        self.tenant_id = metadata.get('tenant_id')
        self.org_id = metadata.get('org_id')
        self.name = metadata.get('name')
        self.created_at = metadata.get('created_at', datetime.now().isoformat())
        self.updated_at = metadata.get('updated_at', datetime.now().isoformat())

        if not self._is_valid_uuid(self.tenant_id):
            raise ValueError("Invalid tenant_id format. Must be a valid UUIDv4.")
        if not self._is_valid_uuid(self.org_id):
            raise ValueError("Invalid org_id format. Must be a valid UUIDv4.")

    @staticmethod
    def _is_valid_uuid(uuid_str):
        if not isinstance(uuid_str, str):
            return False
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)
        return bool(uuid_pattern.match(uuid_str))

if __name__ == '__main__':
    metadata = {
        'tenant_id': '550e8400-e29b-41d4-a716-446655440000',
        'org_id': '550e8400-e29b-41d4-a716-446655440001',
        'name': 'Test Org'
    }
    tenant_org = TenantOrgModel(metadata)
    assert tenant_org.tenant_id == '550e8400-e29b-41d4-a716-446655440000'
    assert tenant_org.org_id == '550e8400-e29b-41d4-a716-446655440001'
    assert tenant_org.name == 'Test Org'
    print("PASS")