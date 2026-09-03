from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
import psycopg
import pytest
from app.attachments.conversation_models import (
    MAX_CONVERSATION_BYTES,
    MAX_CONVERSATION_FILES,
    MAX_FILE_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_FILES,
)
from app.attachments.conversation_repository import (
    ConversationAttachmentConflict,
    ConversationAttachmentNotFound,
    ConversationAttachmentQuotaExceeded,
    ConversationAttachmentRepository,
    attachment_name_subject,
    attachment_object_subject,
)
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec, SealedContent
from test_control_plane_migration import control_database


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=7,
            purpose="platform-content-encryption",
            _keys={7: b"7" * 32},
        )
    )


@pytest.fixture()
def attachment_database(control_database):
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    other_owner_id = uuid4()
    conversation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Attachment Owner','active'),(%s,'Other Owner','active')",
            (owner_id, other_owner_id),
        )
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','Attachments','active')",
            (conversation_id, owner_id, uuid4()),
        )
    return environment, owner_id, other_owner_id, conversation_id


@pytest.fixture()
def repository(attachment_database) -> ConversationAttachmentRepository:
    environment, _owner_id, _other_owner_id, _conversation_id = attachment_database
    return ConversationAttachmentRepository(
        environment["urls"]["platform_control_app"],
        content_codec=_codec(),
    )


@pytest.mark.postgres
def test_repository_from_config_reads_private_control_dsn_and_applies_limits(
    attachment_database, tmp_path
) -> None:
    environment, owner_id, _other_owner_id, conversation_id = attachment_database
    dsn_file = tmp_path / "control-app-dsn"
    dsn_file.write_text(
        environment["urls"]["platform_control_app"], encoding="utf-8"
    )
    dsn_file.chmod(0o600)
    config = SimpleNamespace(
        attachment_control_database_url_file=str(dsn_file),
        attachment_upload_ttl_seconds=60,
        attachment_max_file_bytes=10,
        attachment_max_conversation_files=2,
        attachment_max_conversation_bytes=20,
        attachment_max_message_files=1,
        attachment_max_message_bytes=10,
    )

    selected = ConversationAttachmentRepository.from_config(
        config, content_codec=_codec()
    )
    upload = selected.create_upload(
        owner_id, conversation_id, "small.txt", "text/plain", 10
    )

    assert timedelta(seconds=59) < upload.expires_at - datetime.now(UTC)
    with pytest.raises(ConversationAttachmentQuotaExceeded):
        selected.create_upload(
            owner_id, conversation_id, "large.txt", "text/plain", 11
        )


@pytest.mark.postgres
def test_create_upload_uses_random_encrypted_object_and_name_metadata(
    attachment_database,
    repository,
) -> None:
    environment, owner_id, _other_owner_id, conversation_id = attachment_database
    original_name = f"candidate-{owner_id}-{conversation_id}.pdf"

    first = repository.create_upload(
        owner_id, conversation_id, original_name, "application/pdf", 123
    )
    second = repository.create_upload(
        owner_id, conversation_id, original_name, "application/pdf", 123
    )

    assert first.upload_id != second.upload_id
    assert first.attachment_id != second.attachment_id
    assert first.original_name == original_name
    assert not hasattr(first, "object_ref")
    assert timedelta(hours=23, minutes=59) < (
        first.expires_at - datetime.now(UTC)
    ) <= timedelta(hours=24)

    target_one = repository.upload_target(owner_id, first.upload_id)
    target_two = repository.upload_target(owner_id, second.upload_id)
    assert target_one.object_ref != target_two.object_ref
    for forbidden in (str(owner_id), str(conversation_id), original_name, "hr-agent"):
        assert forbidden not in target_one.object_ref

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version "
            "from platform_attachments.attachments where attachment_id=%s",
            (first.attachment_id,),
        ).fetchone()
    assert original_name.encode() not in bytes(row[0])
    assert target_one.object_ref.encode() not in bytes(row[2])
    assert repository.content_codec.unseal_json(
        attachment_name_subject(first.attachment_id),
        SealedContent(bytes(row[0]), row[1]),
    ) == {"original_name": original_name}
    assert repository.content_codec.unseal_json(
        attachment_object_subject(first.attachment_id),
        SealedContent(bytes(row[2]), row[3]),
    ) == {"object_ref": target_one.object_ref}


@pytest.mark.postgres
def test_finalize_rejects_size_mismatch_wrong_owner_and_expired_upload(
    attachment_database,
    repository,
) -> None:
    environment, owner_id, other_owner_id, conversation_id = attachment_database
    upload = repository.create_upload(
        owner_id, conversation_id, "resume.pdf", "application/pdf", 10
    )

    with pytest.raises(ConversationAttachmentNotFound):
        repository.complete_upload(other_owner_id, upload.upload_id, 10, b"h" * 32)
    with pytest.raises(ConversationAttachmentConflict, match="size"):
        repository.complete_upload(owner_id, upload.upload_id, 9, b"h" * 32)

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.uploads set expires_at=now()-interval '1 second' "
            "where upload_id=%s",
            (upload.upload_id,),
        )
    with pytest.raises(ConversationAttachmentConflict, match="expired"):
        repository.complete_upload(owner_id, upload.upload_id, 10, b"h" * 32)


@pytest.mark.postgres
def test_complete_is_idempotent_for_the_same_verified_receipt(
    attachment_database,
    repository,
) -> None:
    _environment, owner_id, _other_owner_id, conversation_id = attachment_database
    upload = repository.create_upload(
        owner_id, conversation_id, "resume.pdf", "application/pdf", 10
    )

    first = repository.complete_upload(owner_id, upload.upload_id, 10, b"h" * 32)
    replay = repository.complete_upload(owner_id, upload.upload_id, 10, b"h" * 32)

    assert first == replay
    assert first.state == "validating"
    assert first.size_bytes == 10
    assert first.sha256 == b"h" * 32
    assert repository.completed_attachment(owner_id, upload.upload_id) == first


@pytest.mark.postgres
def test_conversation_count_and_byte_quotas_include_live_upload_reservations(
    attachment_database,
    repository,
) -> None:
    _environment, owner_id, _other_owner_id, conversation_id = attachment_database
    for index in range(MAX_CONVERSATION_FILES):
        repository.create_upload(
            owner_id, conversation_id, f"small-{index}.txt", "text/plain", 1
        )
    with pytest.raises(ConversationAttachmentQuotaExceeded, match="files"):
        repository.create_upload(
            owner_id, conversation_id, "one-too-many.txt", "text/plain", 1
        )

    second_conversation_id = uuid4()
    with psycopg.connect(
        attachment_database[0]["admin"]
    ) as connection:
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','Byte quota','active')",
            (second_conversation_id, owner_id, uuid4()),
        )
    for index in range(MAX_CONVERSATION_BYTES // MAX_FILE_BYTES):
        repository.create_upload(
            owner_id,
            second_conversation_id,
            f"large-{index}.pdf",
            "application/pdf",
            MAX_FILE_BYTES,
        )
    with pytest.raises(ConversationAttachmentQuotaExceeded, match="bytes"):
        repository.create_upload(
            owner_id, second_conversation_id, "overflow.txt", "text/plain", 1
        )


@pytest.mark.postgres
def test_expired_orphan_upload_does_not_consume_conversation_quota(
    attachment_database,
    repository,
) -> None:
    environment, owner_id, _other_owner_id, conversation_id = attachment_database
    uploads = [
        repository.create_upload(
            owner_id, conversation_id, f"orphan-{index}.txt", "text/plain", 1
        )
        for index in range(MAX_CONVERSATION_FILES)
    ]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.uploads set expires_at=now()-interval '1 second' "
            "where upload_id=%s",
            (uploads[0].upload_id,),
        )

    replacement = repository.create_upload(
        owner_id, conversation_id, "replacement.txt", "text/plain", 1
    )

    assert replacement.expires_at > datetime.now(UTC)


@pytest.mark.postgres
def test_message_preparation_enforces_five_file_and_fifty_mb_boundaries(
    attachment_database,
    repository,
) -> None:
    environment, owner_id, _other_owner_id, conversation_id = attachment_database
    uploads = [
        repository.create_upload(
            owner_id,
            conversation_id,
            f"ready-{index}.pdf",
            "application/pdf",
            MAX_MESSAGE_BYTES // MAX_MESSAGE_FILES,
        )
        for index in range(MAX_MESSAGE_FILES + 1)
    ]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.attachments set state='ready',ready_at=now() "
            "where attachment_id=any(%s)",
            ([upload.attachment_id for upload in uploads],),
        )

    selected = repository.prepare_message(
        owner_id,
        conversation_id,
        [upload.attachment_id for upload in uploads[:MAX_MESSAGE_FILES]],
    )
    assert len(selected) == MAX_MESSAGE_FILES
    assert sum(asset.size_bytes for asset in selected) == MAX_MESSAGE_BYTES

    with pytest.raises(ConversationAttachmentQuotaExceeded, match="files"):
        repository.prepare_message(
            owner_id,
            conversation_id,
            [upload.attachment_id for upload in uploads],
        )


@pytest.mark.postgres
def test_list_assets_is_owner_scoped_and_does_not_expose_object_references(
    attachment_database,
    repository,
) -> None:
    _environment, owner_id, other_owner_id, conversation_id = attachment_database
    created = repository.create_upload(
        owner_id, conversation_id, "candidate.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 20
    )

    assets = repository.list_conversation_assets(owner_id, conversation_id)

    assert assets.conversation_id == conversation_id
    assert [asset.attachment_id for asset in assets.attachments] == [
        created.attachment_id
    ]
    assert assets.attachments[0].original_name == "candidate.docx"
    assert not hasattr(assets.attachments[0], "object_ref")
    with pytest.raises(ConversationAttachmentNotFound):
        repository.list_conversation_assets(other_owner_id, conversation_id)
