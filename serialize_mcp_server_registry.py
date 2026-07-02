from pydantic import BaseModel, ValidationError
from typing import List, Dict

class McpServerRegistryEntry(BaseModel):
    server_id: str
    name: str
    endpoint: str
    trust_score: float
    metadata: Dict = {}

def serialize_registry_rows(rows: List[Dict]) -> List[McpServerRegistryEntry]:
    entries = []
    for row in rows:
        try:
            entry = McpServerRegistryEntry(**row)
            entries.append(entry)
        except ValidationError as e:
            raise ValidationError(f"Invalid row: {row}") from e
    return entries

if __name__ == "__main__":
    # Representative row dicts
    rows = [
        {
            "server_id": "srv1",
            "name": "Server 1",
            "endpoint": "http://server1.example.com",
            "trust_score": 0.95,
            "metadata": {"region": "us-west"}
        },
        {
            "server_id": "srv2",
            "name": "Server 2",
            "endpoint": "http://server2.example.com",
            "trust_score": 0.85,
            "metadata": {"region": "us-east"}
        },
        {
            "server_id": "srv3",
            "name": "Server 3",
            "endpoint": "http://server3.example.com",
            "trust_score": "invalid_score",  # Intentionally malformed
            "metadata": {"region": "eu-west"}
        }
    ]

    # Expected model instances
    expected_entries = [
        McpServerRegistryEntry(
            server_id="srv1",
            name="Server 1",
            endpoint="http://server1.example.com",
            trust_score=0.95,
            metadata={"region": "us-west"}
        ),
        McpServerRegistryEntry(
            server_id="srv2",
            name="Server 2",
            endpoint="http://server2.example.com",
            trust_score=0.85,
            metadata={"region": "us-east"}
        )
    ]

    # Serialize rows and assert results
    try:
        entries = serialize_registry_rows(rows)
        assert entries == expected_entries, "Serialized entries do not match expected entries"
        print("All rows serialized successfully")
    except ValidationError as e:
        print(f"Validation error: {e}")