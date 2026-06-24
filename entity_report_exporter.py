import uuid

class EntityReportExporter:
    def __init__(self, metadata):
        self.entity_id = metadata.get('entity_id')
        self.report_type = metadata.get('report_type')
        self.format = metadata.get('format')

        if not self._is_valid_uuid(self.entity_id):
            raise ValueError("Invalid entity_id format. Must be UUIDv4.")

    @staticmethod
    def _is_valid_uuid(val):
        try:
            uuid_obj = uuid.UUID(val, version=4)
        except ValueError:
            return False
        return str(uuid_obj) == val

if __name__ == '__main__':
    exporter = EntityReportExporter({
        'entity_id': '550e8400-e29b-41d4-a716-446655440000',
        'report_type': 'security',
        'format': 'pdf'
    })

    assert exporter.entity_id == '550e8400-e29b-41d4-a716-446655440000'
    assert exporter.report_type == 'security'
    assert exporter.format == 'pdf'

    print("PASS")