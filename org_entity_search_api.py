import re

class OrgEntitySearchAPI:
    def __init__(self, metadata):
        self.org_id = metadata.get('org_id')
        self.entity_type = metadata.get('entity_type')
        self.query = metadata.get('query')

        if not self._is_valid_uuid(self.org_id):
            raise ValueError("Invalid org_id format. Expected UUIDv4.")

    def _is_valid_uuid(self, uuid_str):
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)
        return bool(uuid_pattern.match(uuid_str))

if __name__ == '__main__':
    metadata = {
        'org_id': '550e8400-e29b-41d4-a716-446655440000',
        'entity_type': 'user',
        'query': 'test'
    }

    api_instance = OrgEntitySearchAPI(metadata)

    assert api_instance.org_id == '550e8400-e29b-41d4-a716-446655440000'
    assert api_instance.entity_type == 'user'
    assert api_instance.query == 'test'

    print("PASS")