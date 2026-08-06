import logging
from uuid import UUID

import pytest

from app.attachments.models import ResolvedAttachment
from app.attachments.service import (
    AttachmentConflict,
    AttachmentNotFound,
    AttachmentRangeError,
    AttachmentService,
)
from app.attachments.store import AttachmentStore, AttachmentStoreError


ATTACHMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeBody:
    def __init__(self, data=b"payload", *, fail=False):
        self.data = data
        self.fail = fail
        self.closed = False

    def read(self, size):
        assert size == 1024 * 1024
        if self.fail:
            raise OSError("stream broke")
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeRepository:
    def __init__(self, issued=None, resolved=None):
        self.issued = issued
        self.resolved = resolved
        self.audit = []
        self.resolve_calls = []

    def issue_ticket(self, attachment_id, purpose, ttl):
        self.issue_call = (attachment_id, purpose, ttl)
        return self.issued

    def resolve_ticket(self, ticket, context):
        self.resolve_calls.append((ticket, context))
        return self.resolved

    def record_access(self, resolved, result, context):
        self.audit.append((resolved.attachment_id, resolved.purpose, result, context))


class FakeStore:
    def __init__(self, body=None, *, content_length=7):
        self.body = body or FakeBody()
        self.content_length = content_length
        self.calls = []

    def open(self, resolved, byte_range):
        self.calls.append((resolved, byte_range))
        return self.body, self.content_length


def resolved(mime="application/pdf", purpose="preview", name="report.pdf", size=7):
    return ResolvedAttachment(
        attachment_id=ATTACHMENT_ID,
        purpose=purpose,
        display_name=name,
        mime_type=mime,
        size_bytes=size,
        bucket="orbbec-agent-attachments",
        object_key="sha256/private-object",
        sha256="a" * 64,
    )


def test_missing_attachment_returns_not_found_without_s3() -> None:
    repository = FakeRepository(issued=None)
    store = FakeStore()
    service = AttachmentService(repository, store, ticket_seconds=9999)

    with pytest.raises(AttachmentNotFound):
        service.issue_ticket(ATTACHMENT_ID, "download")

    assert store.calls == []


def test_ticket_service_caps_configured_ttl_to_300() -> None:
    issued = object()
    repository = FakeRepository(issued=issued)
    service = AttachmentService(repository, FakeStore(), ticket_seconds=9999)

    assert service.issue_ticket(ATTACHMENT_ID, "download") is issued
    assert repository.issue_call == (ATTACHMENT_ID, "download", 300)


def test_unsupported_preview_instructs_download_without_opening_s3() -> None:
    repository = FakeRepository(resolved=resolved(mime="text/html"))
    store = FakeStore()
    service = AttachmentService(repository, store, ticket_seconds=300)

    with pytest.raises(AttachmentConflict, match="download"):
        service.open_content("opaque", None, {})

    assert store.calls == []


@pytest.mark.parametrize(
    ("mime", "purpose", "expected"),
    [
        ("image/png", "preview", "inline"),
        ("application/pdf", "preview", "inline"),
        ("application/msword", "download", "attachment"),
        ("application/vnd.ms-excel", "download", "attachment"),
        ("application/vnd.ms-powerpoint", "download", "attachment"),
        ("application/zip", "download", "attachment"),
        ("text/html", "download", "attachment"),
        ("application/x-executable", "download", "attachment"),
        ("application/octet-stream", "download", "attachment"),
    ],
)
def test_disposition_is_derived_from_database_mime_and_purpose(
    mime, purpose, expected
):
    repository = FakeRepository(resolved=resolved(mime=mime, purpose=purpose))
    result = AttachmentService(repository, FakeStore(), 300).open_content(
        "opaque", None, {}
    )

    assert result.headers["Content-Disposition"].startswith(expected + ";")


def test_safe_disposition_and_response_headers_strip_header_injection() -> None:
    repository = FakeRepository(
        resolved=resolved(name='r\r\néport "final".pdf')
    )
    result = AttachmentService(repository, FakeStore(), 300).open_content(
        "opaque", None, {}
    )

    disposition = result.headers["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert 'filename="report _final_.pdf"' in disposition
    assert "filename*=UTF-8''r%C3%A9port%20%22final%22.pdf" in disposition
    assert result.headers["Cache-Control"] == "private, no-store"
    assert result.headers["X-Content-Type-Options"] == "nosniff"
    assert result.headers["Content-Security-Policy"] == "sandbox"


def test_valid_range_returns_partial_result() -> None:
    repository = FakeRepository(resolved=resolved(size=100, purpose="download"))
    store = FakeStore(content_length=10)
    result = AttachmentService(repository, store, 300).open_content(
        "opaque", "bytes=10-19", {"request_id": "req"}
    )

    assert result.status_code == 206
    assert result.headers["Content-Range"] == "bytes 10-19/100"
    assert result.headers["Content-Length"] == "10"
    assert store.calls[0][1] == (10, 19)


@pytest.mark.parametrize(
    "value",
    ["bytes=abc", "bytes=0-1,3-4", "items=0-1", "bytes=99-2"],
)
def test_malformed_or_multiple_ranges_are_rejected(value) -> None:
    repository = FakeRepository(resolved=resolved(size=100, purpose="download"))
    store = FakeStore()
    with pytest.raises(AttachmentRangeError):
        AttachmentService(repository, store, 300).open_content("opaque", value, {})
    assert store.calls == []


@pytest.mark.parametrize(
    ("fail", "result"), [(False, "streamed"), (True, "stream_failed")]
)
def test_stream_always_closes_and_audits_without_sensitive_logs(
    caplog, fail, result
) -> None:
    body = FakeBody(fail=fail)
    repository = FakeRepository(resolved=resolved(name="private-name.pdf"))
    service = AttachmentService(repository, FakeStore(body), 300)
    caplog.set_level(logging.INFO)
    opened = service.open_content("secret-ticket", None, {"request_id": "req-1"})

    if fail:
        with pytest.raises(OSError):
            list(opened.stream)
    else:
        assert b"".join(opened.stream) == b"payload"

    assert body.closed is True
    assert repository.audit[0][2] == result
    logs = caplog.text
    for sensitive in ("secret-ticket", "private-name.pdf", "sha256/private-object"):
        assert sensitive not in logs


def test_size_mismatch_fails_before_streaming_and_is_audited() -> None:
    repository = FakeRepository(resolved=resolved(size=7))
    store = FakeStore(content_length=6)

    with pytest.raises(AttachmentConflict, match="size"):
        AttachmentService(repository, store, 300).open_content("opaque", None, {})

    assert repository.audit[0][2] == "size_mismatch"


class ReadOnlyS3:
    def __init__(self, total=100, returned=10):
        self.total = total
        self.returned = returned
        self.calls = []
        self.body = FakeBody(b"x" * returned)

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        return {"ContentLength": self.total}

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"ContentLength": self.returned, "Body": self.body}


def test_store_uses_only_head_and_get_with_database_coordinates() -> None:
    s3 = ReadOnlyS3()
    store = AttachmentStore(s3)

    body, length = store.open(resolved(size=100), (10, 19))

    assert body is s3.body and length == 10
    assert s3.calls == [
        (
            "head_object",
            {
                "Bucket": "orbbec-agent-attachments",
                "Key": "sha256/private-object",
            },
        ),
        (
            "get_object",
            {
                "Bucket": "orbbec-agent-attachments",
                "Key": "sha256/private-object",
                "Range": "bytes=10-19",
            },
        ),
    ]
    assert not any(
        hasattr(AttachmentStore, name)
        for name in (
            "list",
            "put",
            "write",
            "delete",
            "list_objects",
            "put_object",
            "delete_object",
        )
    )


def test_store_size_mismatch_does_not_get_object() -> None:
    s3 = ReadOnlyS3(total=99)

    with pytest.raises(AttachmentStoreError, match="size mismatch"):
        AttachmentStore(s3).open(resolved(size=100), None)

    assert [name for name, _kwargs in s3.calls] == ["head_object"]
