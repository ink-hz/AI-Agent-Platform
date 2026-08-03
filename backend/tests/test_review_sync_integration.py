from unittest.mock import Mock

import pytest

from app.review.models import NegativeFeedbackGroup
from app.sync_remote import importer
from app.sync_remote.importer import ReviewBackfillError, SyncResult


SOURCE_RESULT = SyncResult(
    run_id="00000000-0000-0000-0000-000000000001",
    source_kind="fae",
    status="succeeded",
    source_counts={"turn_feedback": 2},
    applied_counts={"turn_feedback": 2},
    validation={"orphan_turn_sessions": 0},
)


class ReviewRepository:
    def __init__(self):
        self.groups = [
            NegativeFeedbackGroup("ai-fae-agent", "fae:existing", "old", ("fae:f1",)),
            NegativeFeedbackGroup("ai-fae-agent", "fae:new", "new", ("fae:f2",)),
        ]
        self.calls = []

    def list_negative_feedback_groups(self):
        return self.groups

    def backfill_negative_group(self, group, *, actor):
        self.calls.append((group.turn_key, actor))
        return (group.turn_key == "fae:new", group.turn_key == "fae:new", group.turn_key == "fae:new")


def test_successful_sync_enqueues_only_new_negative_turns(monkeypatch):
    repository = ReviewRepository()
    monkeypatch.setattr(importer, "import_bundle", Mock(return_value=SOURCE_RESULT))

    result = importer.import_bundle_with_review(
        "sync-dsn",
        object(),
        review_repository=repository,
        actor="codex",
    )

    assert result.source_sync == SOURCE_RESULT
    assert result.review_backfill.created_issues == 1
    assert result.review_backfill.created_links == 1
    assert result.review_backfill.created_events == 1
    assert repository.calls == [("fae:existing", "codex"), ("fae:new", "codex")]


def test_failed_source_import_never_runs_review_backfill(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(
        importer,
        "import_bundle",
        Mock(side_effect=importer.ImportValidationError("invalid")),
    )

    with pytest.raises(importer.ImportValidationError):
        importer.import_bundle_with_review(
            "sync-dsn",
            object(),
            review_repository=repository,
            actor="codex",
        )

    repository.list_negative_feedback_groups.assert_not_called()


def test_review_failure_preserves_successful_source_result(monkeypatch):
    repository = Mock()
    repository.list_negative_feedback_groups.side_effect = RuntimeError("review down")
    monkeypatch.setattr(importer, "import_bundle", Mock(return_value=SOURCE_RESULT))

    with pytest.raises(ReviewBackfillError) as error:
        importer.import_bundle_with_review(
            "sync-dsn",
            object(),
            review_repository=repository,
            actor="codex",
        )

    assert error.value.source_sync.status == "succeeded"
    assert error.value.reason == "review_backfill_failed"
