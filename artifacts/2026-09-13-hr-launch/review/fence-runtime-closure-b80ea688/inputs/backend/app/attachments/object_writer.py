from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.config import Config
from app.local_secrets import SecretFileUnavailable, read_secret_file

from .conversation_models import MAX_FILE_BYTES, ObjectReceipt
from .s3_erasure import erase_s3_object

_READ_CHUNK_BYTES = 1024 * 1024


class AttachmentObjectWriterError(RuntimeError):
    pass


class AttachmentObjectWriterSizeMismatch(AttachmentObjectWriterError):
    pass


def _stage_stream(body: BinaryIO, staged: BinaryIO, expected_size: int) -> ObjectReceipt:
    size = 0
    digest = hashlib.sha256()
    while size < expected_size:
        chunk = body.read(min(_READ_CHUNK_BYTES, expected_size - size))
        if not isinstance(chunk, bytes):
            raise TypeError("attachment stream must return bytes")
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise AttachmentObjectWriterSizeMismatch(
                "attachment object size mismatch"
            )
        staged.write(chunk)
        digest.update(chunk)
    extra = body.read(1)
    if not isinstance(extra, bytes):
        raise TypeError("attachment stream must return bytes")
    if size != expected_size or extra:
        raise AttachmentObjectWriterSizeMismatch(
            "attachment object size mismatch"
        )
    staged.seek(0)
    return ObjectReceipt(size, digest.digest())


def _credential(path_value: str) -> str:
    path = Path(path_value)
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise AttachmentObjectWriterError(
                "attachment object credential unavailable"
            )
        return read_secret_file(str(path))
    except AttachmentObjectWriterError:
        raise
    except (OSError, SecretFileUnavailable):
        raise AttachmentObjectWriterError(
            "attachment object credential unavailable"
        ) from None


class AttachmentObjectWriter:
    def __init__(
        self, client, bucket: str, *, max_file_bytes: int = MAX_FILE_BYTES
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("attachment bucket invalid")
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
            or max_file_bytes > MAX_FILE_BYTES
        ):
            raise ValueError("attachment file limit invalid")
        self._client = client
        self._bucket = bucket
        self._max_file_bytes = max_file_bytes

    @classmethod
    def from_config(cls, config: Config) -> AttachmentObjectWriter:
        import boto3
        from botocore.config import Config as BotoConfig

        client = boto3.client(
            "s3",
            endpoint_url=config.attachment_s3_endpoint,
            region_name="us-east-1",
            aws_access_key_id=_credential(
                config.attachment_s3_access_key_file
            ),
            aws_secret_access_key=_credential(
                config.attachment_s3_secret_key_file
            ),
            config=BotoConfig(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 2},
            ),
        )
        return cls(
            client,
            config.attachment_s3_bucket,
            max_file_bytes=config.attachment_max_file_bytes,
        )

    def put_stream(
        self, object_ref: str, body: BinaryIO, expected_size: int
    ) -> ObjectReceipt:
        if not isinstance(object_ref, str) or not object_ref:
            raise ValueError("attachment object reference invalid")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
            or expected_size > self._max_file_bytes
        ):
            raise AttachmentObjectWriterSizeMismatch(
                "attachment object size mismatch"
            )
        with tempfile.SpooledTemporaryFile(max_size=_READ_CHUNK_BYTES) as staged:
            try:
                receipt = _stage_stream(body, staged, expected_size)
            except AttachmentObjectWriterSizeMismatch:
                raise
            except Exception:  # noqa: BLE001 - sanitize arbitrary stream errors
                raise AttachmentObjectWriterError(
                    "attachment object write failed"
                ) from None
            try:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=object_ref,
                    Body=staged,
                    ContentLength=expected_size,
                    IfNoneMatch="*",
                )
                if not isinstance(response, dict):
                    raise AttachmentObjectWriterError(
                        "attachment object write failed"
                    )
            except AttachmentObjectWriterError:
                raise
            except Exception:  # noqa: BLE001 - sanitize arbitrary client errors
                raise AttachmentObjectWriterError(
                    "attachment object write failed"
                ) from None
            return receipt

    def delete(self, object_ref: str) -> None:
        if not isinstance(object_ref, str) or not object_ref:
            raise ValueError("attachment object reference invalid")
        try:
            erase_s3_object(self._client, self._bucket, object_ref)
        except Exception:  # noqa: BLE001 - storage clients are injected
            raise AttachmentObjectWriterError(
                "attachment object delete failed"
            ) from None
