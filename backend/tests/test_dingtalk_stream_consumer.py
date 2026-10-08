from __future__ import annotations

import asyncio
import logging

import pytest
from dingtalk_stream import AckMessage, EventMessage

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.event_worker import ClaimedStreamEvent, DirectoryEventWorker
from app.control_plane.stream_consumer import (
    APPROVED_ORGANIZATION_EVENT_TYPES,
    DurableOrganizationEventHandler,
    StreamConsumer,
    StreamInboxRepository,
    StreamPayloadCipher,
)
from test_control_plane_migration import control_database


APPROVED_EVENTS = frozenset(
    {
        "user_add_org",
        "user_modify_org",
        "user_leave_org",
        "org_user_active",
        "org_dept_create",
        "org_dept_modify",
        "org_dept_remove",
    }
)


def _keyring() -> IdentityKeyring:
    return IdentityKeyring(
        active_version=7,
        purpose="provider-encryption",
        _keys={7: b"e" * 32},
    )


def _event(
    *,
    event_type: str = "user_modify_org",
    event_id: str = "event-sensitive-001",
    corp_id: str = "corp-expected",
) -> EventMessage:
    event = EventMessage()
    event.headers.event_type = event_type
    event.headers.event_id = event_id
    event.headers.event_corp_id = corp_id
    event.headers.event_born_time = 1_786_665_000_000
    event.headers.message_id = "message-sensitive-001"
    event.data = {"UserId": ["provider-user-sensitive"], "name": "Sensitive Name"}
    return event


class RecordingInbox:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.committed = False
        self.fail = False

    def insert_encrypted_once(self, **row) -> bool:
        if self.fail:
            raise RuntimeError("database unavailable")
        inserted = row["event_key"] not in self.rows
        self.rows.setdefault(row["event_key"], row)
        self.committed = True
        return inserted


@pytest.mark.asyncio
async def test_handler_acks_only_after_the_encrypted_inbox_commit() -> None:
    inbox = RecordingInbox()
    handler = DurableOrganizationEventHandler(
        inbox,
        StreamPayloadCipher(_keyring()),
        expected_corp_id="corp-expected",
    )

    code, message = await handler.process(_event())

    assert (code, message) == (AckMessage.STATUS_OK, "OK")
    assert inbox.committed is True
    assert len(inbox.rows) == 1
    row = next(iter(inbox.rows.values()))
    assert row["event_type"] == "user_modify_org"
    assert row["encryption_key_version"] == 7
    assert b"provider-user-sensitive" not in row["encrypted_payload"]
    assert "event-sensitive-001" not in row["event_key"]


@pytest.mark.asyncio
async def test_handler_propagates_persistence_failure_instead_of_acking() -> None:
    inbox = RecordingInbox()
    inbox.fail = True
    handler = DurableOrganizationEventHandler(
        inbox,
        StreamPayloadCipher(_keyring()),
        expected_corp_id="corp-expected",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await handler.process(_event())

    assert inbox.committed is False


@pytest.mark.asyncio
async def test_duplicate_delivery_conflicts_harmlessly_on_one_opaque_key() -> None:
    inbox = RecordingInbox()
    handler = DurableOrganizationEventHandler(
        inbox,
        StreamPayloadCipher(_keyring()),
        expected_corp_id="corp-expected",
    )

    assert (await handler.process(_event()))[0] == AckMessage.STATUS_OK
    assert (await handler.process(_event()))[0] == AckMessage.STATUS_OK

    assert len(inbox.rows) == 1


def test_payload_cipher_round_trips_with_event_metadata_as_aad() -> None:
    cipher = StreamPayloadCipher(_keyring())
    payload = {
        "event_type": "user_add_org",
        "event_id": "opaque-at-rest",
        "corp_id": "corp-expected",
        "born_time_ms": 1_786_665_000_000,
        "data": {"UserId": ["provider-user-sensitive"]},
    }

    sealed = cipher.seal(payload, event_key="a" * 64, event_type="user_add_org")

    assert sealed.key_version == 7
    assert b"provider-user-sensitive" not in sealed.ciphertext
    assert cipher.open(
        sealed.ciphertext,
        key_version=sealed.key_version,
        event_key="a" * 64,
        event_type="user_add_org",
    ) == payload
    with pytest.raises(ValueError, match="stream payload unavailable"):
        cipher.open(
            sealed.ciphertext,
            key_version=sealed.key_version,
            event_key="b" * 64,
            event_type="user_add_org",
        )


def test_organization_event_allowlist_is_exact() -> None:
    assert APPROVED_ORGANIZATION_EVENT_TYPES == APPROVED_EVENTS | {"user_active_org"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["user_add_org", "user_active_org", "user_leave_org"])
async def test_live_stream_payload_reaches_directory_worker(event_type) -> None:
    inbox = RecordingInbox()
    cipher = StreamPayloadCipher(_keyring())
    event = _event(event_type=event_type)
    event.data = {"userId": ["provider-user-sensitive"], "timeStamp": 1_786_665_000_000}
    await DurableOrganizationEventHandler(
        inbox, cipher, expected_corp_id="corp-expected"
    ).process(event)
    stored = next(iter(inbox.rows.values()))
    claimed = ClaimedStreamEvent(
        inbox_id=1, event_key=stored["event_key"], event_type=stored["event_type"],
        encrypted_payload=stored["encrypted_payload"],
        encryption_key_version=stored["encryption_key_version"], attempts=1,
    )

    class DirectoryRepository:
        actions = []

        def claim_next(self):
            return claimed

        def apply_departure(self, **values):
            self.actions.append("departure")
            return "applied"

        def mark_processed(self, inbox_id):
            self.actions.append("processed")

        def heartbeat(self, status, error_code=None):
            self.actions.append(status)

        def reschedule(self, *args):
            self.actions.append("retry")

    class Refresher:
        users = []

        async def refresh_user(self, userid):
            self.users.append(userid)

    repository, refresher = DirectoryRepository(), Refresher()
    worker = DirectoryEventWorker(
        repository, cipher, member_refresher=refresher, reconciler=None
    )

    await worker.process_once()

    assert "processed" in repository.actions
    assert "retry" not in repository.actions
    if event_type == "user_leave_org":
        assert "departure" in repository.actions
    else:
        assert refresher.users == ["provider-user-sensitive"]


@pytest.mark.asyncio
async def test_unknown_event_is_persisted_encrypted_for_safe_ignore() -> None:
    inbox = RecordingInbox()
    handler = DurableOrganizationEventHandler(
        inbox,
        StreamPayloadCipher(_keyring()),
        expected_corp_id="corp-expected",
    )

    code, _ = await handler.process(_event(event_type="unexpected_topic"))

    assert code == AckMessage.STATUS_OK
    assert next(iter(inbox.rows.values()))["event_type"] == "unapproved"


@pytest.mark.asyncio
async def test_handler_logs_only_safe_error_code(caplog) -> None:
    inbox = RecordingInbox()
    inbox.fail = True
    handler = DurableOrganizationEventHandler(
        inbox,
        StreamPayloadCipher(_keyring()),
        expected_corp_id="corp-expected",
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        await handler.process(_event())

    rendered = caplog.text
    assert "event_persist_failed" in rendered
    assert "provider-user-sensitive" not in rendered
    assert "event-sensitive-001" not in rendered
    assert "Sensitive Name" not in rendered


class FakeSdkClient:
    def __init__(self, credential, logger=None) -> None:
        self.credential = credential
        self.logger = logger
        self.event_handler = None
        self.callback_registrations: list[tuple] = []
        self.started = False

    def register_all_event_handler(self, handler) -> None:
        self.event_handler = handler

    def register_callback_handler(self, *values) -> None:
        self.callback_registrations.append(values)

    async def start(self) -> None:
        self.started = True


@pytest.mark.asyncio
async def test_consumer_uses_the_official_event_channel_without_callback_wildcards() -> None:
    created: list[FakeSdkClient] = []

    def factory(credential, logger=None):
        client = FakeSdkClient(credential, logger)
        created.append(client)
        return client

    consumer = StreamConsumer(
        app_key="public-app-key",
        app_secret="secret-from-file",
        handler=DurableOrganizationEventHandler(
            RecordingInbox(),
            StreamPayloadCipher(_keyring()),
            expected_corp_id="corp-expected",
        ),
        client_factory=factory,
    )

    await consumer.run()

    assert created[0].started is True
    assert created[0].event_handler is not None
    assert created[0].callback_registrations == []
    assert 1 <= consumer.RECONNECT_MIN_SECONDS <= consumer.RECONNECT_MAX_SECONDS <= 30


@pytest.mark.asyncio
async def test_consumer_stop_cancels_the_pinned_sdks_reconnect_loop() -> None:
    started = asyncio.Event()

    class Websocket:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class BlockingSdkClient(FakeSdkClient):
        def __init__(self, credential, logger=None) -> None:
            super().__init__(credential, logger)
            self.websocket = Websocket()

        async def start(self) -> None:
            started.set()
            await asyncio.Future()

    consumer = StreamConsumer(
        app_key="public-app-key",
        app_secret="secret-from-file",
        handler=DurableOrganizationEventHandler(
            RecordingInbox(),
            StreamPayloadCipher(_keyring()),
            expected_corp_id="corp-expected",
        ),
        client_factory=BlockingSdkClient,
    )
    run_task = asyncio.create_task(consumer.run())
    await started.wait()

    await consumer.stop()

    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert consumer._client.websocket.closed is True


@pytest.mark.postgres
def test_stream_repository_commits_once_before_duplicate_ack(control_database) -> None:
    environment = control_database["environments"]["production"]
    repository = StreamInboxRepository(
        environment["urls"]["platform_stream_ingest"]
    )

    values = {
        "event_key": "c" * 64,
        "event_type": "user_modify_org",
        "encrypted_payload": b"encrypted-payload-not-provider-data",
        "encryption_key_version": 7,
    }

    assert repository.insert_encrypted_once(**values) is True
    assert repository.insert_encrypted_once(**values) is False

    import psycopg

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select count(*),min(status),min(attempts) "
            "from platform_control.stream_inbox where event_key=%s",
            (values["event_key"],),
        ).fetchone()
    assert row == (1, "pending", 0)
