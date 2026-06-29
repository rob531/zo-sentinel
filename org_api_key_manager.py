import hashlib
import os
import random
import string
from typing import Optional, Tuple

class APIKeyManager:
    def __init__(self, keys_file: str = 'api_keys.txt'):
        self.keys_file = keys_file
        self._ensure_keys_file()
        self.rate_limits = {}  # key_id: (timestamp, count)

    def _ensure_keys_file(self):
        if not os.path.exists(self.keys_file):
            with open(self.keys_file, 'w') as f:
                os.chmod(self.keys_file, 0o600)

    def _generate_key(self, length: int = 32) -> str:
        chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def issue_key(self, org_id: str, label: str) -> Tuple[str, str]:
        key = self._generate_key()
        key_hash = self._hash_key(key)

        with open(self.keys_file, 'a') as f:
            f.write(f"{key_hash},{org_id},{label}\n")

        return key_hash, key

    def revoke_key(self, key_id: str) -> bool:
        try:
            with open(self.keys_file, 'r') as f:
                lines = f.readlines()

            with open(self.keys_file, 'w') as f:
                for line in lines:
                    if not line.startswith(key_id):
                        f.write(line)

            if self.rate_limits.get(key_id):
                del self.rate_limits[key_id]

            return True
        except FileNotFoundError:
            return False

    def verify_key(self, key: str) -> Optional[str]:
        key_hash = self._hash_key(key)

        try:
            with open(self.keys_file, 'r') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) == 3 and parts[0] == key_hash:
                        return parts[1]  # org_id
        except FileNotFoundError:
            pass

        return None

    def rate_limit_check(self, key_id: str, limit: int = 100, window: int = 60) -> bool:
        now = int(time.time())

        if key_id not in self.rate_limits:
            self.rate_limits[key_id] = (now, 1)
            return True

        timestamp, count = self.rate_limits[key_id]

        if now - timestamp > window:
            self.rate_limits[key_id] = (now, 1)
            return True

        if count < limit:
            self.rate_limits[key_id] = (timestamp, count + 1)
            return True

        return False

if __name__ == "__main__":
    manager = APIKeyManager()

    # Issue a key
    org_id = "test_org"
    label = "test_label"
    key_id, key = manager.issue_key(org_id, label)

    # Verify the key resolves the org
    assert manager.verify_key(key) == org_id

    # Revoke the key
    manager.revoke_key(key_id)

    # Assert verify returns None
    assert manager.verify_key(key) is None

    print("PASS")