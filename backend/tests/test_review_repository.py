from uuid import UUID
import inspect

import pytest

from app.review.repository import (
    ISSUE_UPDATE_FIELDS,
    ConcurrentUpdate,
    PsycopgReviewRepository,
    require_row_version,
)


def test_row_version_is_mandatory_for_issue_updates():
    current = {"id": UUID(int=1), "row_version": 2}

    require_row_version(current, 2)
    with pytest.raises(ConcurrentUpdate) as error:
        require_row_version(current, 1)

    assert error.value.current == current


def test_repository_has_no_manual_close_or_status_write_surface():
    assert "status" not in ISSUE_UPDATE_FIELDS
    assert not hasattr(PsycopgReviewRepository, "close_issue")
    assert not hasattr(PsycopgReviewRepository, "set_status")


def test_repository_exposes_transactional_closure_inputs():
    for method in (
        "create_issue",
        "update_issue",
        "link_turn",
        "move_link",
        "merge_issue",
        "mark_fix_ready",
        "add_evidence",
        "record_evidence_verification",
        "create_or_get_replay",
        "finish_replay",
        "review_replay",
        "set_disposition",
        "get_issue_detail",
        "list_inbox",
        "overview",
        "recalculate_and_record_transition",
    ):
        assert hasattr(PsycopgReviewRepository, method)


def test_progress_recalculation_reads_gates_and_writes_event_in_one_transaction():
    source = inspect.getsource(
        PsycopgReviewRepository.recalculate_and_record_transition
    )

    assert source.count("self._connection()") == 1
    assert "lock_issue=True" in source
    assert "self._event(" in source
