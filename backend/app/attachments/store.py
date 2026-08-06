from pathlib import Path
import stat

from app.config import Config
from app.local_secrets import SecretFileUnavailable, read_secret_file

from .models import ResolvedAttachment


class AttachmentStoreError(RuntimeError):
    pass


def read_mode_0600(path: str) -> str:
    try:
        if stat.S_IMODE(Path(path).lstat().st_mode) != 0o600:
            raise AttachmentStoreError("attachment credential unavailable")
        return read_secret_file(path)
    except (OSError, SecretFileUnavailable) as error:
        raise AttachmentStoreError("attachment credential unavailable") from error


class AttachmentStore:
    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: Config):
        import boto3
        from botocore.config import Config as BotoConfig

        client = boto3.client(
            "s3",
            endpoint_url=config.attachment_s3_endpoint,
            region_name="us-east-1",
            aws_access_key_id=read_mode_0600(
                config.attachment_s3_access_key_file
            ),
            aws_secret_access_key=read_mode_0600(
                config.attachment_s3_secret_key_file
            ),
            config=BotoConfig(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 2},
            ),
        )
        return cls(client)

    def open(
        self,
        resolved: ResolvedAttachment,
        byte_range: tuple[int, int] | None,
    ):
        try:
            head = self._client.head_object(
                Bucket=resolved.bucket, Key=resolved.object_key
            )
            if int(head["ContentLength"]) != resolved.size_bytes:
                raise AttachmentStoreError("attachment size mismatch")
            request = {"Bucket": resolved.bucket, "Key": resolved.object_key}
            if byte_range is not None:
                request["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
            response = self._client.get_object(**request)
            return response["Body"], int(response["ContentLength"])
        except AttachmentStoreError:
            raise
        except Exception as error:
            raise AttachmentStoreError("attachment storage unavailable") from error
