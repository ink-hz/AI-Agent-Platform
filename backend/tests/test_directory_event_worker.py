from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.identity import IdentityResolver
from app.control_plane.event_worker import (
    ClaimedStreamEvent,
    DirectoryEventRepository,
    DirectoryEventWorker,
    StreamEventDisposition,
    TargetedMemberRefresher,
)
from test_control_plane_migration import control_database
from test_identity_crypto import _codec


NOW = datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc)


def _claimed(
    event_type: str,
    *,
    event_key: str = "a" * 64,
    attempts: int = 1,
    payload: bytes = b"ciphertext",
) -> ClaimedStreamEvent:
    return ClaimedStreamEvent(
        inbox_id=1,
        event_key=event_key,
        event_type=event_type,
        encrypted_payload=payload,
        encryption_key_version=1,
        attempts=attempts,
    )


class FakeCipher:
    def __init__(self, payload: dict | None = None, *, fail: bool = False) -> None:
        self.payload = payload or {}
        self.fail = fail

    def open(self, ciphertext, *, key_version, event_key, event_type):
        if self.fail:
            raise ValueError("stream payload unavailable")
        return self.payload


class FakeRepository:
    def __init__(self, items: list[ClaimedStreamEvent]) -> None:
        self.items = list(items)
        self.actions: list[tuple] = []
        self.departure_result = StreamEventDisposition.APPLIED

    def claim_next(self):
        return self.items.pop(0) if self.items else None

    def apply_departure(self, **values):
        self.actions.append(("departure", values))
        return self.departure_result

    def mark_processed(self, inbox_id):
        self.actions.append(("processed", inbox_id))

    def mark_ignored(self, inbox_id, reason):
        self.actions.append(("ignored", inbox_id, reason))

    def reschedule(self, inbox_id, error_code, delay_seconds):
        self.actions.append(("retry", inbox_id, error_code, delay_seconds))

    def mark_dead_letter(self, inbox_id, error_code):
        self.actions.append(("dead", inbox_id, error_code))

    def heartbeat(self, status, error_code=None):
        self.actions.append(("heartbeat", status, error_code))


class FakeRefresher:
    def __init__(self) -> None:
        self.users: list[str] = []

    async def refresh_user(self, userid: str) -> None:
        self.users.append(userid)


class FakeReconciler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_full(self, run_kind: str) -> None:
        self.calls.append(run_kind)


class FakeDingTalkClient:
    def __init__(self, returned_userid: str = "provider-user-sensitive") -> None:
        self.returned_userid = returned_userid
        self.requests: list[str] = []

    async def get_member(self, userid: str):
        self.requests.append(userid)
        return type("Member", (), {"userid": self.returned_userid})()


def _payload(event_type: str, *, born_time_ms: int = 1_786_665_000_000) -> dict:
    return {
        "event_type": event_type,
        "event_id": "event-sensitive-001",
        "corp_id": "corp-expected",
        "born_time_ms": born_time_ms,
        "data": {"UserId": ["provider-user-sensitive"]},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["user_add_org", "user_modify_org", "org_user_active"])
async def test_member_add_change_and_activation_use_targeted_refresh(event_type) -> None:
    repo = FakeRepository([_claimed(event_type)])
    refresher = FakeRefresher()
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload(event_type)),
        member_refresher=refresher,
        reconciler=FakeReconciler(),
    )

    assert await worker.process_once() is True

    assert refresher.users == ["provider-user-sensitive"]
    assert ("processed", 1) in repo.actions


@pytest.mark.asyncio
async def test_targeted_refresh_verifies_member_then_promotes_an_atomic_snapshot() -> None:
    client = FakeDingTalkClient()
    reconciler = FakeReconciler()
    refresher = TargetedMemberRefresher(client, reconciler)

    await refresher.refresh_user("provider-user-sensitive")

    assert client.requests == ["provider-user-sensitive"]
    assert reconciler.calls == ["targeted"]


@pytest.mark.asyncio
async def test_targeted_refresh_rejects_provider_identity_mismatch() -> None:
    refresher = TargetedMemberRefresher(
        FakeDingTalkClient("different-provider-user"),
        FakeReconciler(),
    )

    with pytest.raises(RuntimeError, match="targeted member refresh failed"):
        await refresher.refresh_user("provider-user-sensitive")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["org_dept_create", "org_dept_modify", "org_dept_remove"])
async def test_department_events_trigger_one_safe_event_reconciliation(event_type) -> None:
    repo = FakeRepository([_claimed(event_type)])
    reconciler = FakeReconciler()
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload(event_type)),
        member_refresher=FakeRefresher(),
        reconciler=reconciler,
    )

    await worker.process_once()

    assert reconciler.calls == ["event"]
    assert ("processed", 1) in repo.actions


@pytest.mark.asyncio
async def test_departure_is_one_immediate_idempotent_repository_operation() -> None:
    repo = FakeRepository([_claimed("user_leave_org")])
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload("user_leave_org")),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
    )

    started = time.monotonic()
    await worker.process_once()

    elapsed = time.monotonic() - started
    departure = next(action for action in repo.actions if action[0] == "departure")
    assert departure[1]["userid"] == "provider-user-sensitive"
    assert departure[1]["event_key"] == "a" * 64
    assert departure[1]["event_time"] == datetime.fromtimestamp(
        1_786_665_000, tz=timezone.utc
    )
    assert ("processed", 1) in repo.actions
    assert elapsed < 30


@pytest.mark.asyncio
async def test_out_of_order_departure_is_ignored_without_reversing_newer_state() -> None:
    repo = FakeRepository([_claimed("user_leave_org")])
    repo.departure_result = StreamEventDisposition.STALE
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload("user_leave_org", born_time_ms=1)),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
    )

    await worker.process_once()

    assert ("ignored", 1, "stale_event") in repo.actions
    assert ("processed", 1) not in repo.actions


@pytest.mark.asyncio
async def test_unapproved_event_is_safely_ignored_without_handler_dispatch() -> None:
    repo = FakeRepository([_claimed("unapproved")])
    refresher = FakeRefresher()
    reconciler = FakeReconciler()
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload("unknown")),
        member_refresher=refresher,
        reconciler=reconciler,
    )

    await worker.process_once()

    assert ("ignored", 1, "event_unapproved") in repo.actions
    assert refresher.users == []
    assert reconciler.calls == []


@pytest.mark.asyncio
async def test_poison_event_retries_then_moves_to_encrypted_dead_letter_row() -> None:
    retry_repo = FakeRepository([_claimed("user_modify_org", attempts=1)])
    retry_worker = DirectoryEventWorker(
        retry_repo,
        FakeCipher(fail=True),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
        max_attempts=3,
    )

    await retry_worker.process_once()

    assert ("retry", 1, "payload_invalid", 2) in retry_repo.actions

    dead_repo = FakeRepository([_claimed("user_modify_org", attempts=3)])
    dead_worker = DirectoryEventWorker(
        dead_repo,
        FakeCipher(fail=True),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
        max_attempts=3,
    )

    await dead_worker.process_once()

    assert ("dead", 1, "payload_invalid") in dead_repo.actions


@pytest.mark.asyncio
async def test_duplicate_or_post_commit_redelivery_has_one_effective_transition() -> None:
    item = _claimed("user_modify_org")
    repo = FakeRepository([item])
    refresher = FakeRefresher()
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(_payload("user_modify_org")),
        member_refresher=refresher,
        reconciler=FakeReconciler(),
    )

    assert await worker.process_once() is True
    assert await worker.process_once() is False

    assert refresher.users == ["provider-user-sensitive"]
    assert repo.actions.count(("processed", 1)) == 1


@pytest.mark.asyncio
async def test_worker_heartbeat_is_separate_from_directory_freshness() -> None:
    repo = FakeRepository([])
    worker = DirectoryEventWorker(
        repo,
        FakeCipher(),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
    )

    assert await worker.process_once() is False

    assert repo.actions == [("heartbeat", "healthy", None)]


@pytest.mark.asyncio
async def test_worker_service_polls_without_busy_loop_and_propagates_shutdown() -> None:
    repo = FakeRepository([])
    sleeps: list[float] = []

    async def stop_after_first_idle(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    worker = DirectoryEventWorker(
        repo,
        FakeCipher(),
        member_refresher=FakeRefresher(),
        reconciler=FakeReconciler(),
        idle_poll_seconds=0.5,
        sleep=stop_after_first_idle,
    )

    with pytest.raises(asyncio.CancelledError):
        await worker.serve()

    assert sleeps == [0.5]
    assert repo.actions == [("heartbeat", "healthy", None)]


@pytest.mark.postgres
def test_database_claim_lease_recovers_and_heartbeat_is_independent(
    control_database,
    tmp_path,
) -> None:
    environment = control_database["environments"]["production"]
    repository = DirectoryEventRepository(
        environment["urls"]["platform_directory_worker"],
        identity_codec=_codec(tmp_path),
        corp_id="test-corp",
        lease_seconds=1,
    )
    event_key = "d" * 64
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.stream_inbox "
            "(event_key,event_type,encrypted_payload,encryption_key_version) "
            "values (%s,'user_modify_org',%s,1)",
            (event_key, b"opaque"),
        )

    first = repository.claim_next()
    assert first is not None
    assert first.event_key == event_key
    assert first.attempts == 1
    assert repository.claim_next() is None

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.stream_inbox set available_at=now()-interval '1 second' "
            "where event_key=%s",
            (event_key,),
        )
    recovered = repository.claim_next()
    assert recovered is not None
    assert recovered.attempts == 2

    repository.heartbeat("healthy", None)
    with psycopg.connect(environment["admin"]) as connection:
        heartbeat = connection.execute(
            "select status,last_error_code,last_seen_at is not null "
            "from platform_control.worker_heartbeats "
            "where worker_name='dingtalk-directory-event'"
        ).fetchone()
    assert heartbeat == ("healthy", None, True)


@pytest.mark.postgres
def test_departure_atomically_invalidates_user_and_revokes_every_session(
    control_database,
    tmp_path,
) -> None:
    environment = control_database["environments"]["production"]
    codec = _codec(tmp_path)
    repository = DirectoryEventRepository(
        environment["urls"]["platform_directory_worker"],
        identity_codec=codec,
        corp_id="test-corp",
    )
    generation_id = uuid4()
    internal_user_id = uuid4()
    userid = "departure-provider-user"
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        IdentityResolver.corporate_provider_id("test-corp", userid),
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,0,%s,now())",
            (generation_id, "e" * 64),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,last_confirmed_generation_id) "
            "values (%s,'Departure Test','active',%s)",
            (internal_user_id, generation_id),
        )
        connection.execute(
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,'employee',%s,%s,%s,%s)",
            (
                uuid4(),
                internal_user_id,
                corporate.lookup_hmac,
                corporate.lookup_key_version,
                corporate.ciphertext,
                corporate.encryption_key_version,
            ),
        )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status,"
            "union_lookup_hmac,union_lookup_key_version) "
            "values (%s,%s,%s,'employee',%s,%s,%s,%s,'Departure Test','active',%s,%s)",
            (
                generation_id,
                uuid4(),
                internal_user_id,
                corporate.lookup_hmac,
                corporate.lookup_key_version,
                corporate.ciphertext,
                corporate.encryption_key_version,
                b"u" * 32,
                corporate.lookup_key_version,
            ),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s,"
            "last_complete_at=now(),updated_at=now() where singleton",
            (generation_id,),
        )
        for index in range(2):
            connection.execute(
                "insert into platform_control.web_sessions "
                "(session_id,internal_user_id,token_hash,token_hash_key_version,"
                "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
                "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",
                (uuid4(), internal_user_id, bytes([index + 1]) * 32, bytes([index + 3]) * 32),
            )

    applied = repository.apply_departure(
        userid=userid,
        event_time=NOW,
        event_key="e" * 64,
    )
    repeated = repository.apply_departure(
        userid=userid,
        event_time=NOW,
        event_key="e" * 64,
    )
    stale = repository.apply_departure(
        userid=userid,
        event_time=datetime(2026, 8, 13, tzinfo=timezone.utc),
        event_key="f" * 64,
    )

    assert applied is StreamEventDisposition.APPLIED
    assert repeated is StreamEventDisposition.ALREADY_APPLIED
    assert stale is StreamEventDisposition.STALE
    with psycopg.connect(environment["admin"]) as connection:
        user = connection.execute(
            "select status,locally_invalidated_at is not null "
            "from platform_control.internal_users where internal_user_id=%s",
            (internal_user_id,),
        ).fetchone()
        sessions = connection.execute(
            "select count(*),count(revoked_at),min(revoked_reason) "
            "from platform_control.web_sessions where internal_user_id=%s",
            (internal_user_id,),
        ).fetchone()
    assert user == ("inactive", True)
    assert sessions == (2, 2, "dingtalk_departure")


@pytest.mark.postgres
def test_departure_revokes_a_mapped_user_missing_from_the_active_snapshot(
    control_database,
    tmp_path,
) -> None:
    environment = control_database["environments"]["production"]
    codec = _codec(tmp_path)
    repository = DirectoryEventRepository(
        environment["urls"]["platform_directory_worker"],
        identity_codec=codec,
        corp_id="test-corp",
    )
    internal_user_id = uuid4()
    userid = "mapped-user-not-in-current-snapshot"
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        IdentityResolver.corporate_provider_id("test-corp", userid),
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Mapped User','active')",
            (internal_user_id,),
        )
        connection.execute(
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,'employee',%s,%s,%s,%s)",
            (
                uuid4(),
                internal_user_id,
                corporate.lookup_hmac,
                corporate.lookup_key_version,
                corporate.ciphertext,
                corporate.encryption_key_version,
            ),
        )
        connection.execute(
            "insert into platform_control.web_sessions "
            "(session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",
            (uuid4(), internal_user_id, b"m" * 32, b"n" * 32),
        )

    assert repository.apply_departure(
        userid=userid,
        event_time=NOW,
        event_key="9" * 64,
    ) is StreamEventDisposition.APPLIED

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status,locally_invalidated_at is not null "
            "from platform_control.internal_users where internal_user_id=%s",
            (internal_user_id,),
        ).fetchone() == ("inactive", True)
        assert connection.execute(
            "select revoked_at is not null from platform_control.web_sessions "
            "where internal_user_id=%s",
            (internal_user_id,),
        ).fetchone() == (True,)
