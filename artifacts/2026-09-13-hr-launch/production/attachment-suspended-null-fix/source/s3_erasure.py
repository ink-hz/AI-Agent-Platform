from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import ClientError

_ERASURE_FENCE_METADATA = {"platform-erasure-fence": "v1"}
_MAX_VERSION_PURGE_SWEEPS = 8
_MAX_VERSION_PAGES = 128
_MAX_VERSION_ENTRIES = 100_000
_VERSION_ENUMERATION_SECONDS = 30.0
_VERSION_PAGE_SIZE = 1000


class S3ObjectErasureError(RuntimeError):
    pass


def _versioning_status(client: Any, bucket: str) -> str | None:
    response = client.get_bucket_versioning(Bucket=bucket)
    if not isinstance(response, dict):
        raise S3ObjectErasureError()
    status = response.get("Status")
    if status is not None and status not in {"Enabled", "Suspended"}:
        raise S3ObjectErasureError()
    return status


def _exact_versions(
    client: Any, bucket: str, object_ref: str
) -> tuple[tuple[str, str], ...]:
    request: dict[str, Any] = {
        "Bucket": bucket,
        "Prefix": object_ref,
        "MaxKeys": _VERSION_PAGE_SIZE,
    }
    entries: list[tuple[str, str]] = []
    seen_entries: set[tuple[str, str]] = set()
    seen_cursors: set[tuple[str, str]] = set()
    pages = 0
    deadline = time.monotonic() + _VERSION_ENUMERATION_SECONDS
    while True:
        if pages >= _MAX_VERSION_PAGES or time.monotonic() >= deadline:
            raise S3ObjectErasureError()
        response = client.list_object_versions(**request)
        pages += 1
        if time.monotonic() >= deadline:
            raise S3ObjectErasureError()
        if not isinstance(response, dict) or not isinstance(
            response.get("IsTruncated"), bool
        ):
            raise S3ObjectErasureError()
        for field, kind in (("Versions", "version"), ("DeleteMarkers", "marker")):
            values = response.get(field, [])
            if not isinstance(values, list):
                raise S3ObjectErasureError()
            for value in values:
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("Key"), str)
                    or not isinstance(value.get("VersionId"), str)
                    or not value["VersionId"]
                ):
                    raise S3ObjectErasureError()
                entry = (kind, value["VersionId"])
                if value["Key"] == object_ref and entry not in seen_entries:
                    seen_entries.add(entry)
                    entries.append(entry)
                    if len(entries) > _MAX_VERSION_ENTRIES:
                        raise S3ObjectErasureError()
        if not response["IsTruncated"]:
            return tuple(entries)
        key_marker = response.get("NextKeyMarker")
        version_marker = response.get("NextVersionIdMarker")
        if (
            not isinstance(key_marker, str)
            or not key_marker
            or not isinstance(version_marker, str)
            or not version_marker
        ):
            raise S3ObjectErasureError()
        cursor = (key_marker, version_marker)
        if cursor in seen_cursors:
            raise S3ObjectErasureError()
        seen_cursors.add(cursor)
        request["KeyMarker"] = key_marker
        request["VersionIdMarker"] = version_marker


def _is_missing_version(error: BaseException) -> bool:
    if not isinstance(error, ClientError):
        return False
    response = error.response
    if not isinstance(response, dict):
        return False
    metadata = response.get("ResponseMetadata")
    detail = response.get("Error")
    return (
        isinstance(metadata, dict)
        and metadata.get("HTTPStatusCode") == 404
        and isinstance(detail, dict)
        and detail.get("Code") in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}
    )


def _is_fence(client: Any, bucket: str, object_ref: str, version_id=None) -> bool:
    request = {"Bucket": bucket, "Key": object_ref}
    if version_id is not None:
        request["VersionId"] = version_id
    response = client.head_object(**request)
    if not isinstance(response, dict):
        raise S3ObjectErasureError()
    length = response.get("ContentLength")
    metadata = response.get("Metadata")
    if version_id is not None:
        reported_version_id = response.get("VersionId")
        if reported_version_id != version_id and not (
            version_id == "null" and "VersionId" not in response
        ):
            raise S3ObjectErasureError()
    return (
        type(length) is int
        and length == 0
        and isinstance(metadata, dict)
        and metadata == _ERASURE_FENCE_METADATA
    )


def _delete_version(client: Any, bucket: str, object_ref: str, version_id: str) -> None:
    try:
        response = client.delete_object(
            Bucket=bucket, Key=object_ref, VersionId=version_id
        )
        if not isinstance(response, dict) or response.get("VersionId") != version_id:
            raise S3ObjectErasureError()
    except Exception as error:
        if not _is_missing_version(error):
            raise


def erase_s3_object(client: Any, bucket: str, object_ref: str) -> None:
    if not isinstance(bucket, str) or not bucket.strip():
        raise S3ObjectErasureError()
    if not isinstance(object_ref, str) or not object_ref:
        raise S3ObjectErasureError()
    try:
        initial_status = _versioning_status(client, bucket)
        response = client.put_object(
            Bucket=bucket,
            Key=object_ref,
            Body=b"",
            ContentLength=0,
            Metadata=dict(_ERASURE_FENCE_METADATA),
        )
        if not isinstance(response, dict):
            raise S3ObjectErasureError()
        if initial_status is None:
            if not _is_fence(client, bucket, object_ref):
                raise S3ObjectErasureError()
            if _versioning_status(client, bucket) is not None:
                raise S3ObjectErasureError()
            return

        for _sweep in range(_MAX_VERSION_PURGE_SWEEPS):
            changed = False
            for kind, version_id in _exact_versions(client, bucket, object_ref):
                if kind == "marker":
                    _delete_version(client, bucket, object_ref, version_id)
                    changed = True
                    continue
                try:
                    fence = _is_fence(
                        client, bucket, object_ref, version_id=version_id
                    )
                except Exception as error:
                    if _is_missing_version(error):
                        continue
                    raise
                if not fence:
                    _delete_version(client, bucket, object_ref, version_id)
                    changed = True
            if changed:
                continue
            if not _is_fence(client, bucket, object_ref):
                raise S3ObjectErasureError()
            if _versioning_status(client, bucket) != initial_status:
                raise S3ObjectErasureError()
            return
        raise S3ObjectErasureError()
    except S3ObjectErasureError:
        raise
    except Exception:  # noqa: BLE001 - injected storage errors are sanitized
        raise S3ObjectErasureError() from None
