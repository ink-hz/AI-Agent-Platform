from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.attachments.retention import (
    AttachmentRetentionService,
    RetentionCandidate,
)


NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


class Repository:
    def __init__(self, candidates):
        self.candidates = candidates
        self.scheduled = []
        self.expired_grants = 0

    def expire_grants(self, *, limit):
        assert limit == 100
        return self.expired_grants

    def due(self, *, limit):
        assert limit == 100
        return self.candidates

    def schedule(self, candidate, reason):
        self.scheduled.append((candidate.attachment_id, reason))


def candidate(*, retained_until, upload_expires_at=None, upload_state=None):
    return RetentionCandidate(
        uuid4(), retained_until, upload_expires_at, upload_state
    )


def test_retention_boundary_is_exactly_365_days_and_archive_does_not_change_it():
    due = candidate(retained_until=NOW)
    future = candidate(retained_until=NOW + timedelta(microseconds=1))
    repository = Repository((due, future))

    scheduled = AttachmentRetentionService(
        repository, clock=lambda: NOW
    ).run_once(limit=100)

    assert scheduled == 1
    assert repository.scheduled == [(due.attachment_id, "retention_expired")]
    assert future.reason(NOW) is None


def test_expired_24_hour_upload_is_scheduled_without_waiting_one_year():
    orphan = candidate(
        retained_until=NOW + timedelta(days=364),
        upload_expires_at=NOW,
        upload_state="uploading",
    )
    repository = Repository((orphan,))

    AttachmentRetentionService(repository, clock=lambda: NOW).run_once()

    assert repository.scheduled == [
        (orphan.attachment_id, "orphan_upload_expired")
    ]


def test_expired_task_grants_are_revoked_in_the_same_maintenance_pass():
    repository = Repository(())
    repository.expired_grants = 2

    changed = AttachmentRetentionService(repository, clock=lambda: NOW).run_once()

    assert changed == 2
