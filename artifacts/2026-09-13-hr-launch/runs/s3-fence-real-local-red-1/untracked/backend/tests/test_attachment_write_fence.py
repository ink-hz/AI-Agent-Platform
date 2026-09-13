from __future__ import annotations

import hashlib
import io
import threading
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from app.attachments.object_writer import (
    AttachmentObjectWriter,
    AttachmentObjectWriterError,
)
from app.attachments.s3_erasure import erase_s3_object
from app.attachments.worker import StoredDerivative
from app.attachments.worker_runtime import (
    AttachmentWorkerRuntimeError,
    S3ProcessingObjectStore,
)

FENCE_METADATA = {"platform-erasure-fence": "v1"}


def _precondition_failed() -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "PutObject",
    )


class FenceS3:
    def __init__(
        self,
        entries=(),
        *,
        status="Enabled",
        page_size=2,
        fail_at=None,
        status_after=None,
    ) -> None:
        self.entries = [dict(entry) for entry in entries]
        self.status = status
        self.status_after = status_after
        self.page_size = page_size
        self.fail_at = fail_at
        self.calls = []
        self._version_counter = 0
        self._status_reads = 0
        self._lock = threading.Lock()

    def _call(self, operation, kwargs):
        self.calls.append((operation, dict(kwargs)))
        if self.fail_at == operation:
            raise OSError(f"{operation} unavailable private-object-token")

    def get_bucket_versioning(self, **kwargs):
        self._call("get_bucket_versioning", kwargs)
        self._status_reads += 1
        status = (
            self.status_after
            if self._status_reads > 1 and self.status_after is not None
            else self.status
        )
        return {} if status is None else {"Status": status}

    def _current(self, key):
        for entry in self.entries:
            if entry["Key"] == key:
                return None if entry["Kind"] == "marker" else entry
        return None

    def _body(self, value):
        if isinstance(value, bytes):
            return value
        chunks = []
        while chunk := value.read(1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)

    def put_object(self, **kwargs):
        self._call("put_object", kwargs)
        key = kwargs["Key"]
        body = self._body(kwargs["Body"])
        with self._lock:
            if kwargs.get("IfNoneMatch") == "*" and self._current(key) is not None:
                raise _precondition_failed()
            metadata = dict(kwargs.get("Metadata", {}))
            if self.status == "Enabled":
                self._version_counter += 1
                version_id = f"written-{self._version_counter}"
                self.entries.insert(
                    0,
                    {
                        "Kind": "version",
                        "Key": key,
                        "VersionId": version_id,
                        "Body": body,
                        "Metadata": metadata,
                    },
                )
                return {"VersionId": version_id}
            self.entries = [entry for entry in self.entries if entry["Key"] != key]
            version_id = "null" if self.status == "Suspended" else None
            self.entries.insert(
                0,
                {
                    "Kind": "version",
                    "Key": key,
                    "VersionId": version_id or "unversioned",
                    "Body": body,
                    "Metadata": metadata,
                },
            )
            return {"VersionId": version_id} if version_id is not None else {}

    def list_object_versions(self, **kwargs):
        self._call("list_object_versions", kwargs)
        prefix = kwargs["Prefix"]
        selected = [entry for entry in self.entries if entry["Key"].startswith(prefix)]
        offset = int(kwargs.get("VersionIdMarker", "0"))
        page = selected[offset : offset + self.page_size]
        result = {
            "IsTruncated": offset + len(page) < len(selected),
            "Versions": [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for entry in page
                if entry["Kind"] == "version"
            ],
            "DeleteMarkers": [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for entry in page
                if entry["Kind"] == "marker"
            ],
        }
        if result["IsTruncated"]:
            result["NextKeyMarker"] = prefix
            result["NextVersionIdMarker"] = str(offset + len(page))
        return result

    def _version(self, key, version_id=None):
        if version_id is None:
            return self._current(key)
        return next(
            (
                entry
                for entry in self.entries
                if entry["Key"] == key
                and entry["VersionId"] == version_id
                and entry["Kind"] == "version"
            ),
            None,
        )

    def head_object(self, **kwargs):
        self._call("head_object", kwargs)
        entry = self._version(kwargs["Key"], kwargs.get("VersionId"))
        if entry is None:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {
            "ContentLength": len(entry["Body"]),
            "Metadata": dict(entry["Metadata"]),
            "VersionId": entry["VersionId"],
            "ETag": '"etag"',
        }

    def get_object(self, **kwargs):
        self._call("get_object", kwargs)
        entry = self._version(kwargs["Key"], kwargs.get("VersionId"))
        if entry is None:
            raise OSError("missing")
        return {
            "ContentLength": len(entry["Body"]),
            "Metadata": dict(entry["Metadata"]),
            "VersionId": entry["VersionId"],
            "Body": io.BytesIO(entry["Body"]),
        }

    def delete_object(self, **kwargs):
        self._call("delete_object", kwargs)
        assert "VersionId" in kwargs, "fenced keys must never receive bare DELETE"
        self.entries = [
            entry
            for entry in self.entries
            if not (
                entry["Key"] == kwargs["Key"]
                and entry["VersionId"] == kwargs["VersionId"]
            )
        ]
        return {"VersionId": kwargs["VersionId"]}

    def exact(self, key):
        return [entry for entry in self.entries if entry["Key"] == key]


def _payload(key, version_id, body=b"personal"):
    return {
        "Kind": "version",
        "Key": key,
        "VersionId": version_id,
        "Body": body,
        "Metadata": {},
    }


def _fence(key, version_id):
    return {
        "Kind": "version",
        "Key": key,
        "VersionId": version_id,
        "Body": b"",
        "Metadata": dict(FENCE_METADATA),
    }


def _marker(key, version_id):
    return {"Kind": "marker", "Key": key, "VersionId": version_id}


@pytest.mark.parametrize("status", ("Enabled", "Suspended", None))
def test_erasure_retains_verified_fence_and_removes_payloads_and_markers(status):
    entries = (
        _payload("owned", "v2"),
        _marker("owned", "m1"),
        _payload("owned", "v1", b""),
        _payload("owned-other", "sibling"),
    )
    client = FenceS3(entries, status=status)

    erase_s3_object(client, "bucket", "owned")

    exact = client.exact("owned")
    assert exact
    assert all(
        entry["Kind"] == "version"
        and entry["Body"] == b""
        and entry["Metadata"] == FENCE_METADATA
        for entry in exact
    )
    assert client.exact("owned-other") == [_payload("owned-other", "sibling")]
    assert all(
        "VersionId" in kwargs
        for operation, kwargs in client.calls
        if operation == "delete_object"
    )


def test_two_erasers_preserve_each_others_verified_fences():
    client = FenceS3((_payload("owned", "v1"),))

    erase_s3_object(client, "bucket", "owned")
    erase_s3_object(client, "bucket", "owned")

    exact = client.exact("owned")
    assert len(exact) == 2
    assert all(entry["Metadata"] == FENCE_METADATA for entry in exact)


@pytest.mark.parametrize("fail_at", ("put_object", "head_object"))
def test_unknown_fence_or_head_result_fails_without_claiming_erasure(fail_at):
    client = FenceS3((_payload("owned", "v1"),), fail_at=fail_at)

    with pytest.raises(Exception):
        erase_s3_object(client, "bucket", "owned")

    assert any(entry["Body"] == b"personal" for entry in client.exact("owned"))


def test_bucket_mode_drift_fails_closed():
    client = FenceS3(
        (_payload("owned", "v1"),), status="Enabled", status_after="Suspended"
    )

    with pytest.raises(Exception):
        erase_s3_object(client, "bucket", "owned")


def test_payload_put_is_conditional_and_cannot_overwrite_fence():
    client = FenceS3((_fence("owned", "f1"),))
    writer = AttachmentObjectWriter(client, "bucket")

    with pytest.raises(AttachmentObjectWriterError):
        writer.put_stream("owned", io.BytesIO(b"personal"), 8)

    assert client.exact("owned") == [_fence("owned", "f1")]
    assert not [call for call in client.calls if call[0] == "delete_object"]


def test_delayed_payload_put_loses_to_fence_without_leaving_payload():
    class Delayed(FenceS3):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def put_object(self, **kwargs):
            if kwargs.get("Metadata") != FENCE_METADATA:
                self.started.set()
                assert self.release.wait(timeout=5)
            return super().put_object(**kwargs)

    client = Delayed()
    writer = AttachmentObjectWriter(client, "bucket")
    outcome = []

    def write():
        try:
            writer.put_stream("owned", io.BytesIO(b"personal"), 8)
        except Exception as error:  # noqa: BLE001 - thread returns exact outcome
            outcome.append(error)

    thread = threading.Thread(target=write)
    thread.start()
    assert client.started.wait(timeout=5)
    erase_s3_object(client, "bucket", "owned")
    client.release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], AttachmentObjectWriterError)
    assert all(entry["Metadata"] == FENCE_METADATA for entry in client.exact("owned"))


def test_derivative_key_is_shared_and_byte_stable():
    import app.attachments.worker as worker

    processing_job_id = UUID("00000000-0000-0000-0000-000000000123")
    expected = hashlib.sha256(
        b"attachment-derivative-v1\0" + processing_job_id.bytes + b"preview"
    ).hexdigest()

    assert worker.derivative_object_key(processing_job_id, "preview") == expected


def test_matching_existing_derivative_is_idempotent_but_fence_is_not_payload():
    key = "a" * 64
    data = b"derived"
    matching = FenceS3((_payload(key, "v1", data),))

    stored = S3ProcessingObjectStore(matching, "bucket").put_derivative(
        data, object_key=key
    )

    assert stored == StoredDerivative(key, len(data), hashlib.sha256(data).digest())
    assert len(matching.exact(key)) == 1

    fenced = FenceS3((_fence(key, "f1"),))
    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(fenced, "bucket").put_derivative(
            data, object_key=key
        )
    assert fenced.exact(key) == [_fence(key, "f1")]


def test_different_existing_derivative_fails_without_delete_or_overwrite():
    key = "a" * 64
    client = FenceS3((_payload(key, "v1", b"other"),))

    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(client, "bucket").put_derivative(
            b"derived", object_key=key
        )

    assert client.exact(key) == [_payload(key, "v1", b"other")]
    assert not [call for call in client.calls if call[0] == "delete_object"]
