"""Owned-PG expiry races; real upload/validation, no fabricated ready state."""

# ruff: noqa: PLC0414
import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic

import psycopg
import pytest
from test_result_artifact_recovery import (
    OwnedObjectStore,
    register_intent,
)
from test_result_artifact_recovery import (
    artifact_content as artifact_content,
)
from test_result_artifact_recovery import (
    artifact_result as artifact_result,
)
from test_result_artifact_recovery import (
    attempt_repository as attempt_repository,
)
from test_result_artifact_recovery import (
    bindings as bindings,
)
from test_result_artifact_recovery import (
    control_database as control_database,
)
from test_result_artifact_recovery import (
    conversation_database as conversation_database,
)
from test_result_artifact_recovery import (
    direct_database as direct_database,
)
from test_result_artifact_recovery import (
    file_warning as file_warning,
)
from test_result_artifact_recovery import (
    intent_changes as intent_changes,
)
from test_result_artifact_recovery import (
    material_adapter as material_adapter,
)
from test_result_artifact_recovery import (
    prepared as prepared,
)
from test_result_artifact_recovery import (
    repository as repository,
)
from test_result_artifact_recovery import (
    signed_api as signed_api,
)
from test_result_artifact_recovery import (
    transport_worker as transport_worker,
)
from test_result_artifact_recovery import (
    worker_conversation as worker_conversation,
)
from test_result_artifact_recovery import (
    worker_turn as worker_turn,
)

from app.attachments.artifact_service import (
    ArtifactOutputService,
    ArtifactRepository,
    ArtifactUploadError,
)
from app.attachments.result_artifact_recovery import ArtifactRecovery
from app.attachments.scanner import TrustedInternalScanner
from app.attachments.validation import AttachmentValidator
from app.attachments.worker import AttachmentProcessor
from app.attachments.worker_runtime import AttachmentProcessingRepository

pytestmark = pytest.mark.postgres


def _wait_until(predicate, *, timeout=5):
    """Poll evidence, not assumed scheduling delays; bounded even on failure."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.02)
    assert predicate(), "owned race did not reach its database barrier"


def test_late_begin_rechecks_original_expiry_after_actual_grant_lock_wait(
    direct_database,
    material_adapter,
    artifact_result,
):
    environment = direct_database[0]
    run_id = artifact_result[1].run_id
    # Enter the pool first: the blocker always releases its transaction before
    # pool shutdown waits for the request, including assertion-failure teardown.
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        psycopg.connect(environment["admin"], autocommit=True) as blocker,
    ):
        with blocker.transaction():
            original_expiry = blocker.execute(
                "update platform_attachments.task_grants "
                "set expires_at=clock_timestamp()+interval '2 seconds' "
                "where task_id=%s and scope='write_output' returning expires_at",
                (run_id,),
            ).fetchone()[0]
            blocker_pid = blocker.info.backend_pid
            pending = pool.submit(
                register_intent, direct_database, material_adapter, artifact_result
            )
            waiter_start = None

            def grant_lock_is_waiting():
                nonlocal waiter_start
                # pg_stat_activity snapshots otherwise remain cached inside this
                # deliberately open transaction and can conceal the waiter.
                blocker.execute("select pg_stat_clear_snapshot()")
                row = blocker.execute(
                    "select xact_start from pg_stat_activity "
                    "where wait_event_type='Lock' "
                    "and %s=any(pg_blocking_pids(pid)) "
                    "and query like 'select * from "
                    "platform_attachments.create_artifact_upload_v64%%'",
                    (blocker_pid,),
                ).fetchone()
                if row is not None:
                    waiter_start = row[0]
                return row is not None

            _wait_until(grant_lock_is_waiting)
            assert waiter_start < original_expiry
            assert not pending.done()
            _wait_until(
                lambda: blocker.execute(
                    "select expires_at<=clock_timestamp() "
                    "from platform_attachments.task_grants "
                    "where task_id=%s and scope='write_output'",
                    (run_id,),
                ).fetchone()[0]
            )
        # Same request, original token/deadline, now unblocked after expiry.
        with pytest.raises(ArtifactUploadError):
            pending.result(timeout=10)
        assert blocker.execute(
            "select file_count,expires_at from platform_attachments.task_grants "
            "where task_id=%s and scope='write_output'",
            (run_id,),
        ).fetchone() == (0, original_expiry)
        assert blocker.execute(
            "select count(*) from platform_attachments.artifacts where task_id=%s",
            (run_id,),
        ).fetchone() == (0,)


def test_stale_uploading_read_cannot_permanently_exclude_late_ready_pdf(
    direct_database,
    material_adapter,
    artifact_result,
    artifact_content,
    repository,
    worker_turn,
    monkeypatch,
):
    environment = direct_database[0]
    run_id, message_id = artifact_result[1].run_id, artifact_result[4]
    upload = register_intent(direct_database, material_adapter, artifact_result)
    assert upload.state == "uploading"
    objects = OwnedObjectStore()
    service = ArtifactOutputService(
        ArtifactRepository(
            environment["urls"]["platform_control_app"],
            content_codec=repository.content_codec,
        ),
        objects,
    )
    original_connection = repository._connection
    crossed = False

    class ObservedConnection:
        """Pause after an actual SELECT, returning its original live PG cursor."""

        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=()):
            nonlocal crossed
            cursor = self.connection.execute(query, params)
            if not crossed and query.startswith("select attachment.*, version.state"):
                # execute() has materialized this SELECT's uploading snapshot;
                # no SQL result or attachment state is stubbed or rewritten.
                assert any(
                    column.name == "upload_state" for column in cursor.description
                )
                crossed = True
                written = service.write(
                    artifact_result[2]["outputWriteGrant"]["bearerToken"],
                    upload.upload_id,
                    io.BytesIO(artifact_content),
                    len(artifact_content),
                )
                assert written.state == "validating"
                # Deterministically cross the grant deadline after the genuine
                # upload commits, before the consumer's separate expiry SELECT.
                with psycopg.connect(environment["admin"]) as connection:
                    connection.execute(
                        "update platform_attachments.task_grants "
                        "set expires_at=clock_timestamp()-interval '1 second' "
                        "where task_id=%s and scope='write_output'",
                        (run_id,),
                    )
            return cursor

    @contextmanager
    def observe_connection():
        with original_connection() as connection:
            yield ObservedConnection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_connection", observe_connection)
        ArtifactRecovery(repository).retry_due()
    assert crossed

    # The consumer's conversation lock is gone. Shared production validation and
    # scanning may now finish; never seed attachment/version ready in this test.
    processor = AttachmentProcessor(
        repository=AttachmentProcessingRepository(
            environment["urls"]["platform_brain_worker"],
            content_codec=repository.content_codec,
        ),
        object_store=objects,
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=None,
        worker_id="owned-result-race-fixture",
    )
    assert asyncio.run(processor.process_next())
    assert asyncio.run(processor.process_next())
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select attachment.state,version.state,version.result_status "
            "from platform_attachments.attachments attachment "
            "join platform_attachments.artifact_versions version using(attachment_id) "
            "where attachment.attachment_id=%s",
            (upload.attachment_id,),
        ).fetchone() == ("ready", "ready", "succeeded")

    # Exercise the actual automatic retry entrypoint, not a manual on_ready()
    # call which could hide the missing production recovery path.
    # Honor a normal five-second pending retry deadline if the fix avoids the
    # stale failure classification; never rewrite retry metadata to force GREEN.
    deadline = monotonic() + 7
    while True:
        ArtifactRecovery(repository).retry_due()
        with psycopg.connect(environment["admin"]) as connection:
            enrichment = connection.execute(
                "select status,last_error_code from platform_control.result_artifact_intents "
                "where run_id=%s",
                (run_id,),
            ).fetchone()
        if enrichment == ("ready", None) or monotonic() >= deadline:
            break
        Event().wait(0.05)
    assert enrichment == ("ready", None)
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_attachments.bindings "
            "where message_id=%s and attachment_id=%s and kind='message_output'",
            (message_id, upload.attachment_id),
        ).fetchone() == (1,)
        assert connection.execute(
            "select assistant_message_id from platform_control.conversation_turns "
            "where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone() == (message_id,)
        assert connection.execute(
            "select count(*) from platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone() == (1,)
    assert ArtifactRecovery(repository).retry_due() == 0
