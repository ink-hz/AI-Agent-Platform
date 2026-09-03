# The imported pytest fixtures intentionally share their public fixture names with
# test parameters throughout this module.
# ruff: noqa: F401, F811

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import psycopg
import pytest
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryConflict,
)
from app.agent_brain.models import load_capability_cards
from app.agent_brain.repository import MissionRepository
from app.attachments.conversation_models import (
    MAX_CONVERSATION_BYTES,
    MAX_CONVERSATION_FILES,
    MAX_MESSAGE_BYTES,
)
from app.attachments.conversation_repository import ConversationAttachmentRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from fastapi.testclient import TestClient
from test_agent_brain_api import _credentials, _write_credentials
from test_agent_brain_conversation_api import _app
from test_agent_brain_conversation_repository import conversation_database
from test_control_plane_migration import control_database


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=9,
            purpose="platform-content-encryption",
            _keys={9: b"9" * 32},
        )
    )


def _repositories(
    environment,
    *,
    hr_supports_attachments: bool = True,
    max_conversation_files: int = MAX_CONVERSATION_FILES,
    max_conversation_bytes: int = MAX_CONVERSATION_BYTES,
) -> tuple[ConversationRepository, ConversationAttachmentRepository]:
    codec = _codec()
    attachment_repository = ConversationAttachmentRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        max_conversation_files=max_conversation_files,
        max_conversation_bytes=max_conversation_bytes,
    )
    cards = tuple(
        card.model_copy(
            update={
                "supports_attachments_in": (
                    hr_supports_attachments if card.agent_id == "hr-bot" else False
                )
            }
        )
        for card in load_capability_cards()
    )
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=missions,
        attachment_repository=attachment_repository,
        agent_capability_cards=cards,
    )
    return conversations, attachment_repository


def _ready_attachment(
    environment,
    attachments: ConversationAttachmentRepository,
    owner_id: UUID,
    conversation_id: UUID | None,
    *,
    name: str = "candidate.pdf",
    size_bytes: int = 7,
) -> UUID:
    upload = attachments.create_upload(
        owner_id,
        conversation_id,
        name,
        "application/pdf",
        size_bytes,
    )
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_attachments.attachments set "
            "detected_mime='application/pdf',coverage_metadata=%s,"
            "sha256=%s,immutable_locator=%s,state='ready',state_reason=null,"
            "ready_at=now() where attachment_id=%s",
            (
                '{"coverage":"first_page","download":true,"inline_preview":true}',
                b"h" * 32,
                f"etag:{uuid4().hex}",
                upload.attachment_id,
            ),
        )
        admin.execute(
            "update platform_attachments.uploads set "
            "detected_mime='application/pdf',coverage_metadata=%s,"
            "sha256=%s,immutable_locator=%s,state='ready',state_reason=null "
            "where attachment_id=%s",
            (
                '{"coverage":"first_page","download":true,"inline_preview":true}',
                b"h" * 32,
                f"etag:{uuid4().hex}",
                upload.attachment_id,
            ),
        )
    return upload.attachment_id


def _complete_turn(environment, turn_id: UUID) -> None:
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_control.conversation_turns set "
            "status='completed',updated_at=now() where turn_id=%s",
            (turn_id,),
        )


def _assert_no_turn_state(environment, request_id: UUID) -> None:
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select (select count(*) from platform_control.conversations "
            "where started_by_client_request_id=%s),"
            "(select count(*) from platform_control.conversation_turns "
            "where client_request_id=%s),"
            "(select count(*) from platform_attachments.bindings binding join "
            "platform_control.conversation_turns turn on turn.turn_id=binding.turn_id "
            "where turn.client_request_id=%s)",
            (request_id, request_id, request_id),
        ).fetchone() == (0, 0, 0)


def test_submission_normalizes_text_and_unordered_attachment_sets() -> None:
    first, second = uuid4(), uuid4()

    submission = ConversationTurnSubmission(
        text="  e\u0301\r\nrequest  ",
        attachment_ids=(second, first),
        active_attachment_ids=(second, first),
    )

    assert submission.text == "é\nrequest"
    assert submission.attachment_ids == tuple(sorted((first, second), key=str))
    assert submission.active_attachment_ids == tuple(
        sorted((first, second), key=str)
    )


@pytest.mark.parametrize(
    "submission",
    [
        lambda: ConversationTurnSubmission("", (), ()),
        lambda: ConversationTurnSubmission("x", (uuid4(),) * 2, (uuid4(),)),
        lambda: ConversationTurnSubmission("x", (), (uuid4(),) * 2),
        lambda: ConversationTurnSubmission(
            "x", tuple(uuid4() for _ in range(6)), tuple(uuid4() for _ in range(6))
        ),
        lambda: ConversationTurnSubmission(
            "x", (), tuple(uuid4() for _ in range(51))
        ),
        lambda: ConversationTurnSubmission("x", (uuid4(),), ()),
    ],
)
def test_submission_rejects_invalid_sets_at_the_backend(submission) -> None:
    with pytest.raises(ValueError):
        submission()


@pytest.mark.postgres
def test_start_claims_unbound_new_attachment_and_projects_only_safe_metadata(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)
    request_id = uuid4()

    result = conversations.start(
        owner_id,
        request_id,
        ConversationTurnSubmission("", (attachment_id,), (attachment_id,)),
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )

    assert result.created is True
    assert result.message.content == ""
    assert result.message.active_attachment_ids == (attachment_id,)
    assert [item.attachment_id for item in result.message.input_attachments] == [
        attachment_id
    ]
    assert result.message.output_attachments == ()
    projected = result.message.input_attachments[0]
    assert projected.display_name == "candidate.pdf"
    assert projected.processing_coverage == {
        "coverage": "first_page",
        "download": True,
        "inline_preview": True,
    }
    assert projected.availability_reason is None
    for forbidden in (
        "object_ref",
        "immutable_locator",
        "original_name_ciphertext",
        "object_ref_ciphertext",
    ):
        assert not hasattr(projected, forbidden)
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select conversation_id from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (result.conversation.conversation_id,)
        assert admin.execute(
            "select conversation_id from platform_attachments.uploads "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (result.conversation.conversation_id,)
        assert {
            row[0]
            for row in admin.execute(
                "select kind from platform_attachments.bindings "
                "where attachment_id=%s",
                (attachment_id,),
            )
        } == {"message_input", "turn_input"}


@pytest.mark.postgres
def test_member_api_serializes_safe_bound_attachment_projection(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)
    app, auth, _agent_use = _app(owner_id, conversations)
    client = TestClient(app)

    response = client.post(
        "/api/v1/agents/hr-bot/conversations",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
        json={
            "text": "",
            "attachment_ids": [str(attachment_id)],
            "active_attachment_ids": [str(attachment_id)],
        },
    )

    assert response.status_code == 201
    message = response.json()["message"]
    assert message["active_attachment_ids"] == [str(attachment_id)]
    assert message["input_attachments"][0]["display_name"] == "candidate.pdf"
    encoded = response.text
    for forbidden in (
        "object_ref",
        "immutable_locator",
        "original_name_ciphertext",
        "object_ref_ciphertext",
    ):
        assert forbidden not in encoded


@pytest.mark.postgres
def test_replay_fingerprint_uses_normalized_text_sets_and_agent_scope(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    first = _ready_attachment(environment, attachments, owner_id, None, name="a.pdf")
    second = _ready_attachment(environment, attachments, owner_id, None, name="b.pdf")
    request_id = uuid4()
    initial = conversations.start(
        owner_id,
        request_id,
        ConversationTurnSubmission(
            "  compare\r\nfiles ", (first, second), (first, second)
        ),
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )

    replay = conversations.start(
        owner_id,
        request_id,
        ConversationTurnSubmission(
            "compare\nfiles", (second, first), (second, first)
        ),
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )

    assert replay.created is False
    assert replay.turn.turn_id == initial.turn.turn_id
    with pytest.raises(ConversationRepositoryConflict):
        conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("changed", (first, second), (first, second)),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
    with pytest.raises(ConversationRepositoryConflict):
        conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("compare\nfiles", (first,), (first,)),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
    with pytest.raises(ConversationRepositoryConflict):
        conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission(
                "compare\nfiles", (first, second), (first, second)
            ),
            mode="direct_agent",
            direct_agent_id="marketing-gtm-bot",
        )


@pytest.mark.postgres
def test_append_can_activate_old_ready_material_without_rebinding_all_session_files(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    started = conversations.start(owner_id, uuid4(), "first")
    _complete_turn(environment, started.turn.turn_id)
    old = _ready_attachment(
        environment, attachments, owner_id, started.conversation.conversation_id
    )
    unused = _ready_attachment(
        environment,
        attachments,
        owner_id,
        started.conversation.conversation_id,
        name="unused.pdf",
    )
    new = _ready_attachment(
        environment,
        attachments,
        owner_id,
        started.conversation.conversation_id,
        name="new.pdf",
    )

    appended = conversations.append_turn(
        owner_id,
        started.conversation.conversation_id,
        uuid4(),
        ConversationTurnSubmission("use selected", (new,), (old, new)),
    )

    assert appended.message.active_attachment_ids == tuple(
        sorted((old, new), key=str)
    )
    assert [item.attachment_id for item in appended.message.input_attachments] == [
        new
    ]
    context = ConversationContextBuilder(conversations).build(
        started.conversation.conversation_id, appended.turn.turn_id
    )
    assert context.active_attachment_ids == tuple(sorted((old, new), key=str))
    assert unused not in context.active_attachment_ids


@pytest.mark.postgres
@pytest.mark.parametrize(
    "invalid_state",
    ["validating", "deleted", "expired", "missing_locator", "erasure"],
)
def test_invalid_attachment_state_rolls_back_the_entire_start(
    conversation_database,
    invalid_state: str,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)
    with psycopg.connect(environment["admin"]) as admin:
        if invalid_state == "validating":
            admin.execute(
                "update platform_attachments.attachments set state='validating',"
                "ready_at=null where attachment_id=%s",
                (attachment_id,),
            )
        elif invalid_state == "deleted":
            admin.execute(
                "update platform_attachments.attachments set state='deleted',"
                "ready_at=null,deleted_at=now() where attachment_id=%s",
                (attachment_id,),
            )
        elif invalid_state == "expired":
            admin.execute(
                "update platform_attachments.attachments set retained_until=now() "
                "where attachment_id=%s",
                (attachment_id,),
            )
        elif invalid_state == "missing_locator":
            admin.execute(
                "update platform_attachments.attachments set immutable_locator=null "
                "where attachment_id=%s",
                (attachment_id,),
            )
        else:
            admin.execute(
                "insert into platform_attachments.erasure_jobs("
                "erasure_job_id,attachment_id,requested_by_internal_user_id,"
                "reason_ciphertext,reason_key_version,reason_sha256) "
                "values (%s,%s,%s,%s,1,%s)",
                (uuid4(), attachment_id, owner_id, b"r" * 29, b"h" * 32),
            )
    request_id = uuid4()

    with pytest.raises(ConversationRepositoryConflict):
        conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("analyze", (attachment_id,), (attachment_id,)),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )

    _assert_no_turn_state(environment, request_id)


@pytest.mark.postgres
def test_wrong_owner_and_wrong_conversation_fail_atomically(
    conversation_database,
) -> None:
    environment, owner_id, other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    wrong_owner = _ready_attachment(environment, attachments, other_owner_id, None)
    other = conversations.start(owner_id, uuid4(), "other conversation")
    wrong_conversation = _ready_attachment(
        environment, attachments, owner_id, other.conversation.conversation_id
    )

    for attachment_id in (wrong_owner, wrong_conversation):
        request_id = uuid4()
        with pytest.raises(ConversationRepositoryConflict):
            conversations.start(
                owner_id,
                request_id,
                ConversationTurnSubmission(
                    "analyze", (attachment_id,), (attachment_id,)
                ),
                mode="direct_agent",
                direct_agent_id="hr-bot",
            )
        _assert_no_turn_state(environment, request_id)


@pytest.mark.postgres
def test_agent_capability_and_message_byte_quota_reject_without_half_state(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    unsupported, unsupported_attachments = _repositories(
        environment, hr_supports_attachments=False
    )
    attachment_id = _ready_attachment(
        environment, unsupported_attachments, owner_id, None
    )
    request_id = uuid4()
    with pytest.raises(ConversationRepositoryConflict, match="attachments"):
        unsupported.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("analyze", (attachment_id,), (attachment_id,)),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
    _assert_no_turn_state(environment, request_id)

    conversations, attachments = _repositories(environment)
    large = tuple(
        _ready_attachment(
            environment,
            attachments,
            owner_id,
            None,
            name=f"large-{index}.pdf",
            size_bytes=MAX_MESSAGE_BYTES // 2 + 1,
        )
        for index in range(2)
    )
    request_id = uuid4()
    with pytest.raises(ConversationRepositoryConflict, match="quota"):
        conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("analyze", large, large),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
    _assert_no_turn_state(environment, request_id)


@pytest.mark.postgres
def test_conversation_quota_is_rechecked_inside_turn_transaction(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment, max_conversation_files=1)
    first = conversations.start(owner_id, uuid4(), "first")
    _complete_turn(environment, first.turn.turn_id)
    one = _ready_attachment(
        environment, attachments, owner_id, first.conversation.conversation_id
    )
    two = _ready_attachment(
        environment,
        ConversationAttachmentRepository(
            environment["urls"]["platform_control_app"], content_codec=_codec()
        ),
        owner_id,
        first.conversation.conversation_id,
        name="two.pdf",
    )
    request_id = uuid4()

    with pytest.raises(ConversationRepositoryConflict, match="quota"):
        conversations.append_turn(
            owner_id,
            first.conversation.conversation_id,
            request_id,
            ConversationTurnSubmission("use one", (one,), (one,)),
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_control.conversation_turns "
            "where client_request_id=%s",
            (request_id,),
        ).fetchone() == (0,)
        assert two != one


@pytest.mark.postgres
def test_concurrent_replay_creates_one_turn_and_one_binding_set(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)
    request_id = uuid4()

    def submit(reverse: bool):
        selected = (attachment_id,)
        return conversations.start(
            owner_id,
            request_id,
            ConversationTurnSubmission("concurrent", selected, selected),
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (False, True)))

    assert sorted(result.created for result in results) == [False, True]
    assert len({result.turn.turn_id for result in results}) == 1
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_attachments.bindings "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_concurrent_distinct_starts_cannot_claim_one_attachment_twice(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)

    def submit(_index: int):
        try:
            return conversations.start(
                owner_id,
                uuid4(),
                ConversationTurnSubmission(
                    "concurrent claim", (attachment_id,), (attachment_id,)
                ),
                mode="direct_agent",
                direct_agent_id="hr-bot",
            )
        except ConversationRepositoryConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, ConversationRepositoryConflict) for result in results) == 1
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_control.conversation_turns"
        ).fetchone() == (1,)
        assert admin.execute(
            "select count(*) from platform_attachments.bindings "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_state_flip_while_binding_waits_fails_closed_without_turn(
    conversation_database,
) -> None:
    environment, owner_id, _other_owner_id = conversation_database
    conversations, attachments = _repositories(environment)
    attachment_id = _ready_attachment(environment, attachments, owner_id, None)
    request_id = uuid4()
    started, finished = Event(), Event()
    outcome: list[BaseException | object] = []

    def submit() -> None:
        started.set()
        try:
            outcome.append(
                conversations.start(
                    owner_id,
                    request_id,
                    ConversationTurnSubmission(
                        "analyze", (attachment_id,), (attachment_id,)
                    ),
                    mode="direct_agent",
                    direct_agent_id="hr-bot",
                )
            )
        except BaseException as error:  # noqa: BLE001 - captured for thread assertion
            outcome.append(error)
        finally:
            finished.set()

    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "select 1 from platform_attachments.attachments "
            "where attachment_id=%s for update",
            (attachment_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(submit)
            assert started.wait(2)
            assert not finished.wait(0.2)
            admin.execute(
                "update platform_attachments.attachments set state='deleted',"
                "ready_at=null,deleted_at=now() where attachment_id=%s",
                (attachment_id,),
            )
            admin.commit()
            future.result(timeout=5)

    assert len(outcome) == 1
    assert isinstance(outcome[0], ConversationRepositoryConflict)
    _assert_no_turn_state(environment, request_id)
