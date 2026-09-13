from __future__ import annotations

import hashlib
from uuid import UUID


def derivative_object_key(processing_job_id: UUID, derivative_kind: str) -> str:
    if not isinstance(processing_job_id, UUID):
        raise ValueError("attachment processing job invalid")
    if not isinstance(derivative_kind, str) or not derivative_kind:
        raise ValueError("attachment derivative kind invalid")
    try:
        encoded_kind = derivative_kind.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("attachment derivative kind invalid") from None
    return hashlib.sha256(
        b"attachment-derivative-v1\0" + processing_job_id.bytes + encoded_kind
    ).hexdigest()
