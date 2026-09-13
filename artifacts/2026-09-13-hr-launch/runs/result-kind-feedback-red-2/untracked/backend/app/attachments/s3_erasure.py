from __future__ import annotations

from typing import Any

_MAX_VERSION_PURGE_SWEEPS = 8
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


def _exact_version_identifiers(
    client: Any, bucket: str, object_ref: str
) -> tuple[str, ...]:
    request: dict[str, Any] = {
        "Bucket": bucket,
        "Prefix": object_ref,
        "MaxKeys": _VERSION_PAGE_SIZE,
    }
    identifiers: list[str] = []
    seen_identifiers: set[str] = set()
    seen_cursors: set[tuple[str, str]] = set()
    while True:
        response = client.list_object_versions(**request)
        if not isinstance(response, dict) or not isinstance(
            response.get("IsTruncated"), bool
        ):
            raise S3ObjectErasureError()
        for field in ("Versions", "DeleteMarkers"):
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
                if (
                    value["Key"] == object_ref
                    and value["VersionId"] not in seen_identifiers
                ):
                    seen_identifiers.add(value["VersionId"])
                    identifiers.append(value["VersionId"])
        if not response["IsTruncated"]:
            return tuple(identifiers)
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


def erase_s3_object(client: Any, bucket: str, object_ref: str) -> None:
    if not isinstance(bucket, str) or not bucket.strip():
        raise S3ObjectErasureError()
    if not isinstance(object_ref, str) or not object_ref:
        raise S3ObjectErasureError()
    try:
        if _versioning_status(client, bucket) is None:
            client.delete_object(Bucket=bucket, Key=object_ref)
            return
        for _sweep in range(_MAX_VERSION_PURGE_SWEEPS):
            identifiers = _exact_version_identifiers(client, bucket, object_ref)
            if not identifiers:
                return
            for version_id in identifiers:
                client.delete_object(
                    Bucket=bucket,
                    Key=object_ref,
                    VersionId=version_id,
                )
        if _exact_version_identifiers(client, bucket, object_ref):
            raise S3ObjectErasureError()
    except S3ObjectErasureError:
        raise
    except Exception:  # noqa: BLE001 - injected storage errors are sanitized
        raise S3ObjectErasureError() from None
