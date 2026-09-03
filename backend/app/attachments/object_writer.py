from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import BinaryIO

from botocore.exceptions import BotoCoreError, ClientError

from app.config import Config
from app.local_secrets import SecretFileUnavailable, read_secret_file

from .conversation_models import MAX_FILE_BYTES, ObjectReceipt

_READ_CHUNK_BYTES = 1024 * 1024


class AttachmentObjectWriterError(RuntimeError):
    pass


class AttachmentObjectWriterSizeMismatch(AttachmentObjectWriterError):
    pass


class _DigestingReader:
    def __init__(self, body: BinaryIO, expected_size: int) -> None:
        self._body = body
        self._expected_size = expected_size
        self._size = 0
        self._digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        remaining = self._expected_size - self._size
        if remaining <= 0:
            return b""
        requested = min(
            remaining,
            _READ_CHUNK_BYTES if size is None or size < 0 else size,
            _READ_CHUNK_BYTES,
        )
        chunk = self._body.read(requested)
        if not isinstance(chunk, bytes):
            raise TypeError("attachment stream must return bytes")
        self._size += len(chunk)
        self._digest.update(chunk)
        return chunk

    def receipt(self) -> ObjectReceipt:
        extra = self._body.read(1)
        if not isinstance(extra, bytes):
            raise TypeError("attachment stream must return bytes")
        if self._size != self._expected_size or extra:
            raise AttachmentObjectWriterSizeMismatch(
                "attachment object size mismatch"
            )
        return ObjectReceipt(self._size, self._digest.digest())


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
    except (OSError, SecretFileUnavailable) as error:
        raise AttachmentObjectWriterError(
            "attachment object credential unavailable"
        ) from error


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

    def _best_effort_delete(self, object_ref: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_ref)
        except (BotoCoreError, ClientError, OSError, RuntimeError):
            pass

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
        reader = _DigestingReader(body, expected_size)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_ref,
                Body=reader,
                ContentLength=expected_size,
            )
            return reader.receipt()
        except AttachmentObjectWriterSizeMismatch:
            self._best_effort_delete(object_ref)
            raise
        except Exception as error:
            self._best_effort_delete(object_ref)
            raise AttachmentObjectWriterError(
                "attachment object write failed"
            ) from error

    def delete(self, object_ref: str) -> None:
        if not isinstance(object_ref, str) or not object_ref:
            raise ValueError("attachment object reference invalid")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_ref)
        except Exception as error:
            raise AttachmentObjectWriterError(
                "attachment object delete failed"
            ) from error
