from uuid import UUID
import inspect

import pytest

from app.review.repository import (
    ISSUE_UPDATE_FIELDS,
    ConcurrentUpdate,
    PsycopgReviewRepository,
    require_row_version,
)
from app.review.scope_sql import HISTORICAL_LINK_EVENT_INVALID_SQL


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
        "get_evidence",
        "get_evidence_owner",
        "load_replay_input",
        "get_verified_deployment",
        "expire_stale_replays",
        "create_or_get_replay",
        "finish_replay",
        "get_replay",
        "review_replay",
        "set_disposition",
        "get_issue_detail",
        "list_inbox",
        "get_turn_summaries",
        "import_release_handoff",
        "overview",
        "recalculate_and_record_transition",
    ):
        assert hasattr(PsycopgReviewRepository, method)


def test_progress_recalculation_reads_gates_and_writes_event_in_one_transaction():
    source = inspect.getsource(
        PsycopgReviewRepository.recalculate_and_record_transition
    )
    helper = inspect.getsource(PsycopgReviewRepository._recalculate_with_cursor)

    assert source.count("self._connection()") == 1
    assert "lock_issue=True" in helper
    assert "self._event(" in helper


def test_backfill_reuses_canonical_primary_after_duplicate_merge():
    source = inspect.getsource(PsycopgReviewRepository.backfill_negative_group)

    assert 'issue["canonical_issue_id"]' in source
    assert "where agent_id=%s and source_turn_key=%s and active" in source
    assert "link_role='primary'" in source


def test_turn_summaries_use_one_read_query_and_omit_missing_source_turns():
    statements = []

    class Result:
        def fetchall(self):
            return [
                {
                    "turn_key": "fae:linked",
                    "issue_id": UUID(int=1),
                    "status": "awaiting_replay",
                    "missing_gates": ["replay"],
                    "latest_valid_replay_id": None,
                },
                {
                    "turn_key": "fae:unmanaged",
                    "issue_id": None,
                    "status": "pending_triage",
                    "missing_gates": ["issue"],
                    "latest_valid_replay_id": None,
                },
            ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, parameters):
            statements.append((statement, parameters))
            return Result()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://analyst@db/flywheel",
        connect=lambda *_args, **_kwargs: Connection(),
    )

    summaries = repository.get_turn_summaries(
        ["fae:linked", "fae:unmanaged", "fae:missing"]
    )

    assert len(statements) == 1
    assert "turn.turn_key = any(%s)" in " ".join(statements[0][0].split())
    assert all(keyword not in statements[0][0].lower() for keyword in ("insert ", "update ", "delete "))
    assert summaries[0]["status"] == "awaiting_replay"
    assert summaries[1] == {
        "turn_key": "fae:unmanaged",
        "issue_id": None,
        "status": "pending_triage",
        "missing_gates": ["issue"],
        "latest_valid_replay_id": None,
    }
    assert all(row["turn_key"] != "fae:missing" for row in summaries)


def test_replay_owner_lookup_is_one_metadata_only_read_query():
    statements = []

    class Result:
        def fetchone(self):
            return {"issue_id": UUID(int=1)}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, parameters):
            statements.append((statement, parameters))
            return Result()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://analyst@db/flywheel",
        connect=lambda *_args, **_kwargs: Connection(),
    )

    replay = repository.get_replay(UUID(int=2))

    assert replay == {"issue_id": UUID(int=1)}
    assert len(statements) == 1
    normalized = " ".join(statements[0][0].split()).lower()
    assert normalized.startswith("select issue_id from")
    assert all(keyword not in normalized for keyword in ("insert ", "update ", "delete "))


def test_release_handoff_import_uses_one_writer_transaction():
    source = inspect.getsource(PsycopgReviewRepository.import_release_handoff)
    event_source = inspect.getsource(PsycopgReviewRepository._handoff_event)

    assert source.count("self._connection()") == 1
    assert "canonical_key" in source
    assert "source_turn_key" in source
    assert "feedback_release_handoffs" in source
    assert "feedback_release_handoff_events" in event_source
    assert "turn.agent_id=%s" in source
    assert "similarity" not in source


def test_optional_agent_filters_are_typed_for_postgres_parameters():
    for method in (
        PsycopgReviewRepository.list_inbox,
        PsycopgReviewRepository.list_issues,
        PsycopgReviewRepository.overview,
    ):
        source = inspect.getsource(method)
        assert "%s is null" not in source
        assert "%s::text is null" in source


def test_evidence_owner_lookup_is_metadata_only_and_read_only():
    statements = []

    class Result:
        def fetchone(self):
            return {"issue_id": UUID(int=1)}

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            statements.append((statement, parameters))
            return Result()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://analyst@db/flywheel",
        connect=lambda *_args, **_kwargs: Connection(),
    )

    assert repository.get_evidence_owner(UUID(int=2)) == {"issue_id": UUID(int=1)}
    normalized = " ".join(statements[0][0].lower().split())
    assert normalized.startswith("select issue_id from")
    assert "select *" not in normalized
    assert all(word not in normalized for word in ("insert ", "update ", "delete "))


def test_inbox_link_requires_owning_issue_to_match_feedback_agent():
    source = inspect.getsource(PsycopgReviewRepository.list_inbox)
    normalized = " ".join(source.split())

    assert "join platform_review.feedback_issues linked_issue" in normalized
    assert "linked_issue.agent_id=f.agent_id" in normalized


def test_issue_scope_sql_validates_all_links_replays_and_historical_link_events():
    source = inspect.getsource(PsycopgReviewRepository.agent_issue_scope_valid)
    normalized = " ".join(source.lower().split())
    historical_scope = " ".join(HISTORICAL_LINK_EVENT_INVALID_SQL.lower().split())

    assert "where link.issue_id=issue.id and link.active" not in normalized
    assert "where link.issue_id=issue.id and (" in normalized
    assert "feedback_replay_runs replay" in normalized
    assert "replay.issue_link_id" in normalized
    assert "historical_link_event_invalid_sql" in normalized
    for event_type in (
        "turn_linked",
        "turn_linked_from_release_handoff",
        "link_moved_in",
        "link_moved_out",
    ):
        assert event_type in historical_scope
    assert "is distinct from" in historical_scope


def test_issue_scope_sql_binds_move_direction_link_identity_and_referenced_issues():
    method_source = " ".join(
        inspect.getsource(PsycopgReviewRepository.agent_issue_scope_valid)
        .lower()
        .split()
    )
    source = " ".join(HISTORICAL_LINK_EVENT_INVALID_SQL.lower().split())

    assert "historical_link_event_invalid_sql" in method_source
    assert "event.before->>'id' is distinct from event.after->>'id'" in source
    assert "event.before->>'issue_id' is distinct from issue.id::text" in source
    assert "event.after->>'issue_id' is distinct from issue.id::text" in source
    assert "historical_before_issue.agent_id is distinct from issue.agent_id" in source
    assert "historical_after_issue.agent_id is distinct from issue.agent_id" in source
    assert "historical_link.id is null" in source
    assert "historical_link.agent_id is distinct from issue.agent_id" in source


def test_issue_scope_sql_accepts_only_canonical_merge_relocated_turn_links():
    source = " ".join(HISTORICAL_LINK_EVENT_INVALID_SQL.lower().split())

    assert "historical_link.issue_id is distinct from issue.id" in source
    assert "from canonical_walk merge_walk" in source
    assert "merge_walk.root_id=issue.id" in source
    assert "merge_walk.current_id=historical_link.issue_id" in source
    assert "merge_move.before->>'id'=event.after->>'id'" in source
    assert "merge_move.after->>'id'=event.after->>'id'" in source
