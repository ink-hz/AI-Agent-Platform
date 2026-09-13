from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from app.attachments.erasure import AttachmentErasureService, ErasureJob
from app.attachments.object_writer import (
    AttachmentObjectWriter,
    AttachmentObjectWriterError,
    AttachmentObjectWriterSizeMismatch,
)
from app.attachments.worker_runtime import (
    AttachmentWorkerRuntimeError,
    S3ProcessingObjectStore,
)
from botocore.exceptions import ClientError


class VersionedS3:
    def __init__(
        self,
        entries=(),
        *,
        status="Enabled",
        page_size=2,
        fail_at=None,
        add_after_empty=False,
        keep_adding=False,
    ) -> None:
        self.entries = [dict(entry) for entry in entries]
        self.status = status
        self.page_size = page_size
        self.fail_at = fail_at
        self.add_after_empty = add_after_empty
        self.keep_adding = keep_adding
        self.added = False
        self.calls = []
        self.next_version = 0

    @staticmethod
    def _payload(entry):
        return entry.get("Body", b"payload"), entry.get("Metadata", {})

    def _call(self, operation, kwargs):
        self.calls.append((operation, dict(kwargs)))
        if self.fail_at == operation:
            raise OSError(f"{operation} unavailable with private-object-token")

    def get_bucket_versioning(self, **kwargs):
        self._call("get_bucket_versioning", kwargs)
        return {} if self.status is None else {"Status": self.status}

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

    def delete_object(self, **kwargs):
        self._call("delete_object", kwargs)
        key = kwargs["Key"]
        if self.status is None:
            self.entries = [entry for entry in self.entries if entry["Key"] != key]
            return {}
        version_id = kwargs.get("VersionId")
        if version_id is None:
            self.entries.append(
                {"Kind": "marker", "Key": key, "VersionId": "bare-delete-marker"}
            )
            return {"DeleteMarker": True, "VersionId": "bare-delete-marker"}
        self.entries = [
            entry
            for entry in self.entries
            if not (entry["Key"] == key and entry["VersionId"] == version_id)
        ]
        if not any(
            entry["Key"] == key
            and entry["Kind"] == "version"
            and entry.get("Metadata") != {"platform-erasure-fence": "v1"}
            for entry in self.entries
        ) and (
            self.keep_adding or (self.add_after_empty and not self.added)
        ):
            self.added = True
            self.next_version += 1
            self.entries.insert(
                0,
                {
                    "Kind": "version",
                    "Key": key,
                    "VersionId": f"concurrent-{self.next_version}",
                    "Body": b"late payload",
                    "Metadata": {},
                },
            )
        return {"VersionId": version_id}

    def put_object(self, **kwargs):
        self._call("put_object", kwargs)
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and self.exact(key):
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        entry = {
            "Kind": "version",
            "Key": key,
            "VersionId": "null" if self.status is None else f"put-{len(self.calls)}",
            "Body": body,
            "Metadata": dict(kwargs.get("Metadata", {})),
        }
        if self.status is None:
            self.entries = [item for item in self.entries if item["Key"] != key]
        self.entries.insert(0, entry)
        return {} if self.status is None else {"VersionId": entry["VersionId"]}

    def head_object(self, **kwargs):
        self._call("head_object", kwargs)
        selected = [entry for entry in self.entries if entry["Key"] == kwargs["Key"]]
        version_id = kwargs.get("VersionId")
        if version_id is not None:
            selected = [entry for entry in selected if entry["VersionId"] == version_id]
        selected = [entry for entry in selected if entry["Kind"] == "version"]
        if not selected:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchVersion"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        body, metadata = self._payload(selected[0])
        return {
            "ContentLength": len(body),
            "Metadata": metadata,
            "VersionId": selected[0]["VersionId"],
        }

    def exact(self, key):
        return [entry for entry in self.entries if entry["Key"] == key]


_MISSING_VERSION_ECHO = object()


class SuspendedNullHeadS3(VersionedS3):
    def __init__(
        self, entries=(), *, reported_version=_MISSING_VERSION_ECHO
    ) -> None:
        super().__init__(entries, status="Suspended")
        self.reported_version = reported_version

    def put_object(self, **kwargs):
        self._call("put_object", kwargs)
        key = kwargs["Key"]
        self.entries = [
            entry
            for entry in self.entries
            if not (entry["Key"] == key and entry["VersionId"] == "null")
        ]
        self.entries.insert(
            0,
            {
                "Kind": "version",
                "Key": key,
                "VersionId": "null",
                "Body": kwargs["Body"],
                "Metadata": dict(kwargs["Metadata"]),
            },
        )
        return {}

    def head_object(self, **kwargs):
        response = super().head_object(**kwargs)
        if kwargs.get("VersionId") == "null":
            if self.reported_version is _MISSING_VERSION_ECHO:
                response.pop("VersionId")
            else:
                response["VersionId"] = self.reported_version
        return response


@pytest.mark.parametrize("status", ("Enabled", "Suspended"))
def test_processing_delete_purges_every_exact_version_and_marker_across_pages(status):
    client = VersionedS3(
        (
            {"Kind": "version", "Key": "owned", "VersionId": "v3"},
            {"Kind": "marker", "Key": "owned", "VersionId": "m2"},
            {"Kind": "version", "Key": "owned-other", "VersionId": "sibling"},
            {"Kind": "version", "Key": "owned", "VersionId": "null"},
            {"Kind": "marker", "Key": "owned", "VersionId": "m1"},
        ),
        status=status,
    )

    S3ProcessingObjectStore(client, "private-bucket").delete("owned")

    assert [entry.get("Metadata") for entry in client.exact("owned")] == [
        {"platform-erasure-fence": "v1"}
    ]
    assert client.exact("owned-other") == [
        {"Kind": "version", "Key": "owned-other", "VersionId": "sibling"}
    ]
    listed = [
        kwargs
        for operation, kwargs in client.calls
        if operation == "list_object_versions"
    ]
    assert len(listed) >= 3
    assert all(call["Prefix"] == "owned" for call in listed)
    assert any("KeyMarker" in call and "VersionIdMarker" in call for call in listed)
    deleted = [
        kwargs for operation, kwargs in client.calls if operation == "delete_object"
    ]
    assert {call["VersionId"] for call in deleted} == {"v3", "m2", "null", "m1"}
    assert all(call["Key"] == "owned" for call in deleted)


def test_unversioned_delete_replaces_payload_with_erasure_fence():
    client = VersionedS3(
        ({"Kind": "version", "Key": "owned", "VersionId": "unversioned"},),
        status=None,
    )

    AttachmentObjectWriter(client, "private-bucket").delete("owned")

    assert [entry.get("Metadata") for entry in client.exact("owned")] == [
        {"platform-erasure-fence": "v1"}
    ]
    assert [operation for operation, _kwargs in client.calls] == [
        "get_bucket_versioning",
        "put_object",
        "head_object",
        "get_bucket_versioning",
    ]


def test_suspended_null_fence_head_may_omit_version_echo():
    client = SuspendedNullHeadS3(
        (
            {"Kind": "version", "Key": "owned", "VersionId": "null"},
            {"Kind": "version", "Key": "owned", "VersionId": "historical"},
        ),
    )

    AttachmentObjectWriter(client, "private-bucket").delete("owned")

    assert client.exact("owned") == [
        {
            "Kind": "version",
            "Key": "owned",
            "VersionId": "null",
            "Body": b"",
            "Metadata": {"platform-erasure-fence": "v1"},
        }
    ]
    assert {
        kwargs["VersionId"]
        for operation, kwargs in client.calls
        if operation == "delete_object"
    } == {"historical"}


@pytest.mark.parametrize("reported_version", (None, "other-version"))
def test_suspended_null_fence_head_rejects_conflicting_version_echo(reported_version):
    client = SuspendedNullHeadS3(
        ({"Kind": "version", "Key": "owned", "VersionId": "payload"},),
        reported_version=reported_version,
    )

    with pytest.raises(AttachmentObjectWriterError, match="delete failed"):
        AttachmentObjectWriter(client, "private-bucket").delete("owned")

    assert any(entry["Body"] == b"" for entry in client.exact("owned"))
    assert any(entry["VersionId"] == "payload" for entry in client.exact("owned"))
    assert not [call for call in client.calls if call[0] == "delete_object"]


def test_object_writer_delete_uses_the_same_version_purge():
    client = VersionedS3(
        (
            {"Kind": "version", "Key": "owned", "VersionId": "v1"},
            {"Kind": "marker", "Key": "owned", "VersionId": "m1"},
        )
    )

    AttachmentObjectWriter(client, "private-bucket").delete("owned")

    assert [entry.get("Metadata") for entry in client.exact("owned")] == [
        {"platform-erasure-fence": "v1"}
    ]
    assert {
        kwargs["VersionId"]
        for operation, kwargs in client.calls
        if operation == "delete_object"
    } == {"v1", "m1"}


@pytest.mark.parametrize(
    "fail_at", ("get_bucket_versioning", "list_object_versions", "delete_object")
)
def test_version_erasure_permission_or_transport_failure_never_reports_success(fail_at):
    client = VersionedS3(
        ({"Kind": "version", "Key": "owned", "VersionId": "v1"},),
        fail_at=fail_at,
    )

    with pytest.raises(AttachmentObjectWriterError, match="delete failed") as captured:
        AttachmentObjectWriter(client, "private-bucket").delete("owned")

    assert captured.value.__cause__ is None
    assert "private-object-token" not in str(captured.value)


def test_bad_version_pagination_fails_closed():
    class BadPagination(VersionedS3):
        def list_object_versions(self, **kwargs):
            self._call("list_object_versions", kwargs)
            return {"IsTruncated": True, "Versions": [], "DeleteMarkers": []}

    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(BadPagination(), "private-bucket").delete("owned")


def test_repeated_version_pagination_cursor_fails_closed():
    class RepeatedPagination(VersionedS3):
        def list_object_versions(self, **kwargs):
            self._call("list_object_versions", kwargs)
            return {
                "IsTruncated": True,
                "Versions": [],
                "DeleteMarkers": [],
                "NextKeyMarker": "owned",
                "NextVersionIdMarker": "same",
            }

    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(RepeatedPagination(), "private-bucket").delete("owned")


def test_version_arriving_during_purge_is_removed_by_followup_sweep():
    client = VersionedS3(
        ({"Kind": "version", "Key": "owned", "VersionId": "v1"},),
        add_after_empty=True,
    )

    S3ProcessingObjectStore(client, "private-bucket").delete("owned")

    assert client.added is True
    assert [entry.get("Metadata") for entry in client.exact("owned")] == [
        {"platform-erasure-fence": "v1"}
    ]
    deleted = [
        kwargs["VersionId"]
        for operation, kwargs in client.calls
        if operation == "delete_object"
    ]
    assert deleted == ["v1", "concurrent-1"]


def test_continuous_concurrent_versions_hit_bound_and_fail_closed():
    client = VersionedS3(
        ({"Kind": "version", "Key": "owned", "VersionId": "v1"},),
        keep_adding=True,
    )

    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(client, "private-bucket").delete("owned")

    assert client.exact("owned")
    assert len([call for call in client.calls if call[0] == "delete_object"]) > 1


def test_erasure_service_records_partial_when_version_inventory_fails():
    job = ErasureJob(
        SimpleNamespace(hex="job"), SimpleNamespace(hex="attachment"), ("owned",)
    )

    class Repository:
        def __init__(self):
            self.records = []

        def claim(self, _worker_id):
            return job

        def record(self, selected, *, failed):
            self.records.append((selected, failed))

    repository = Repository()
    store = S3ProcessingObjectStore(
        VersionedS3(
            ({"Kind": "version", "Key": "owned", "VersionId": "v1"},),
            fail_at="list_object_versions",
        ),
        "private-bucket",
    )

    assert AttachmentErasureService(repository, store).process_next("worker") is True
    assert repository.records == [(job, 1)]


def test_orphan_cleanup_does_not_acknowledge_failed_version_inventory():
    attempt = SimpleNamespace(attempt_id="attempt", object_ref="owned")

    class Repository:
        def __init__(self):
            self.acknowledged = []

        def list_orphaned_writes(self, *, limit):
            assert limit == 1
            return (attempt,)

        def acknowledge_orphaned_write(self, attempt_id):
            self.acknowledged.append(attempt_id)

    repository = Repository()
    writer = AttachmentObjectWriter(
        VersionedS3(
            ({"Kind": "version", "Key": "owned", "VersionId": "v1"},),
            fail_at="list_object_versions",
        ),
        "private-bucket",
    )

    from app.attachments.upload_service import AttachmentUploadService

    assert (
        AttachmentUploadService(repository, writer).cleanup_orphaned_writes(limit=1)
        == 0
    )
    assert repository.acknowledged == []


def test_size_mismatch_is_rejected_before_any_versioned_put_or_delete():
    class Client:
        def __init__(self):
            self.puts = []
            self.calls = []

        def put_object(self, **kwargs):
            self.puts.append(kwargs)
            return {"VersionId": "this-write"}

        def delete_object(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    with pytest.raises(AttachmentObjectWriterSizeMismatch):
        AttachmentObjectWriter(client, "private-bucket").put_stream(
            "owned", io.BytesIO(b"extra"), 4
        )

    assert client.puts == []
    assert client.calls == []


def test_ambiguous_failed_put_does_not_issue_destructive_key_delete():
    class Client:
        def __init__(self):
            self.calls = []

        def put_object(self, **_kwargs):
            raise OSError("reply lost")

        def delete_object(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    with pytest.raises(AttachmentObjectWriterError):
        AttachmentObjectWriter(client, "private-bucket").put_stream(
            "owned", io.BytesIO(b"data"), 4
        )

    assert client.calls == []


def test_ambiguous_failed_derivative_put_does_not_issue_destructive_key_delete():
    class Client:
        def __init__(self):
            self.calls = []

        def put_object(self, **_kwargs):
            raise OSError("reply lost")

        def delete_object(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    with pytest.raises(AttachmentWorkerRuntimeError):
        S3ProcessingObjectStore(client, "private-bucket").put_derivative(
            b"data", object_key="a" * 64
        )

    assert client.calls == []
