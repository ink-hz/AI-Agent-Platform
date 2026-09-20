from __future__ import annotations

import json
from uuid import UUID

from ..local_secrets import SecretFileUnavailable, read_secret_file


class AllowlistUnavailable(RuntimeError):
    pass


class PanoramaAccess:
    """Reload the private file on every request so revocation needs no restart."""

    def __init__(self, path: str):
        self.path = path

    def allows(self, internal_user_id: UUID) -> bool:
        if not self.path:
            return False
        try:
            data = json.loads(read_secret_file(self.path))
            if not isinstance(data, dict) or set(data) != {'schema_version', 'internal_user_ids'}:
                raise ValueError
            if type(data['schema_version']) is not int or data['schema_version'] != 1:
                raise ValueError
            ids = data['internal_user_ids']
            if not isinstance(ids, list) or len(ids) > 200:
                raise ValueError
            if any(not isinstance(value, str) or str(UUID(value)) != value for value in ids):
                raise ValueError
            if len(set(ids)) != len(ids):
                raise ValueError
            return str(internal_user_id) in ids
        except (SecretFileUnavailable, ValueError, TypeError, AttributeError):
            raise AllowlistUnavailable('panorama access unavailable') from None
