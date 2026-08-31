from uuid import UUID
import inspect
import threading

import pytest

from app.review.repository import (
    ISSUE_UPDATE_FIELDS,
    ConcurrentUpdate,
    InvalidReviewMutation,
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
        "move_link_has_replay",
        "merge_relocation_has_replay",
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
        PsycopgReviewRepository.list_issue_page,
        PsycopgReviewRepository.overview,
    ):
        source = inspect.getsource(method)
        assert "%s is null" not in source
        assert "%s::text is null" in source


def test_issue_filters_are_applied_to_canonical_lifecycle_before_limit() -> None:
    statements = []

    class Result:
        def fetchall(self): return []
        def fetchone(self): return {"items": [], "total_count": 0}

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            statements.append((" ".join(statement.lower().split()), parameters))
            return Result()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://analyst@db/flywheel",
        connect=lambda *_args, **_kwargs: Connection(),
    )

    repository.list_issues(
        agent_id="ai-fae-agent", status="open", disposition="actionable",
        limit=2, offset=200,
    )

    sql, params = statements[0]
    assert "after->>'status'" in sql
    assert "not in ('closed', 'duplicate', 'not_actionable', 'wont_fix')" in sql
    assert "issue.disposition=%s" in sql
    assert sql.index("not in") < sql.index("limit %s offset %s")
    assert params == ("ai-fae-agent", "ai-fae-agent", "actionable", 2, 200)


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


@pytest.mark.parametrize("operation", ["move", "merge"])
@pytest.mark.parametrize("has_replay", [True, False])
def test_actual_link_relocation_replay_postcondition(operation, has_replay):
    source_id, target_id, link_id = UUID(int=60), UUID(int=61), UUID(int=62)
    source = {"id": source_id, "row_version": 1, "agent_id": "ai-fae-agent"}
    target = {"id": target_id, "row_version": 1, "agent_id": "ai-fae-agent"}
    link = {
        "id": link_id, "issue_id": source_id, "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn", "source_feedback_keys": [], "active": True,
    }

    class Result:
        def __init__(self, one=None, many=None):
            self.one, self.many = one, many

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many or []

    class Cursor:
        mutated = False
        audit_written = False

        def __enter__(self): return self
        def __exit__(self, *_args): return None

        def execute(self, statement, parameters):
            sql = " ".join(statement.lower().split())
            if sql.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if "pg_advisory_xact_lock" in sql:
                return Result({"pg_advisory_xact_lock": None})
            if sql.startswith("with recursive canonical_walk"):
                return Result({"cycle": False})
            if sql.startswith("select * from platform_review.feedback_issues") and "id=any" in sql:
                return Result(many=[source, target])
            if sql.startswith("select * from platform_review.feedback_issues"):
                return Result(source if parameters[0] == source_id else target)
            if sql.startswith("select * from platform_review.feedback_issue_links"):
                if "where id=%s" in sql:
                    return Result(link)
                if "source_turn_key=%s" in sql:
                    return Result(None)
                return Result(many=[link])
            if sql.startswith("select 1 from platform_review.feedback_replay_runs"):
                return Result({"?column?": 1} if has_replay else None)
            if sql.startswith("insert into platform_review.feedback_issue_events"):
                self.audit_written = True
                return Result()
            if sql.startswith("update "):
                self.mutated = True
                if "returning *" in sql:
                    return Result({**link, "issue_id": target_id})
                return Result()
            raise AssertionError(sql)

    cursor = Cursor()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return cursor

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    def relocate():
        if operation == "move":
            return repository.move_link(
                source_id, link_id, target_id, actor="corp:owner", reason="relocate"
            )
        return repository.merge_issue(
            source_id, target_id, expected_row_version=1,
            actor="corp:owner", reason="relocate",
        )

    if has_replay:
        with pytest.raises(InvalidReviewMutation, match="replay"):
            relocate()
        assert cursor.mutated is False
        assert cursor.audit_written is False
    else:
        relocate()
        assert cursor.mutated is True
        assert cursor.audit_written is True


def test_actual_duplicate_merge_keeps_replay_on_deactivated_source_link():
    source_id, target_id = UUID(int=63), UUID(int=64)
    source_link_id, target_link_id, replay_id = UUID(int=65), UUID(int=66), UUID(int=67)
    source = {"id": source_id, "row_version": 1, "agent_id": "ai-fae-agent"}
    target = {"id": target_id, "row_version": 1, "agent_id": "ai-fae-agent"}
    source_link = {
        "id": source_link_id, "issue_id": source_id, "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn", "source_feedback_keys": ["one"],
        "active": True,
    }
    target_link = {
        **source_link, "id": target_link_id, "issue_id": target_id,
        "source_feedback_keys": ["two"],
    }
    replay = {"id": replay_id, "issue_id": source_id, "issue_link_id": source_link_id}

    class Result:
        def __init__(self, one=None, many=None): self.one, self.many = one, many
        def fetchone(self): return self.one
        def fetchall(self): return self.many or []

    class Cursor:
        audit_count = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            sql = " ".join(statement.lower().split())
            if sql.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if "pg_advisory_xact_lock" in sql:
                return Result({"pg_advisory_xact_lock": None})
            if sql.startswith("with recursive canonical_walk"):
                return Result({"cycle": False})
            if sql.startswith("select * from platform_review.feedback_issues") and "id=any" in sql:
                return Result(many=[source, target])
            if sql.startswith("select * from platform_review.feedback_issues"):
                return Result(source if parameters[0] == source_id else target)
            if sql.startswith("select * from platform_review.feedback_issue_links"):
                if "source_turn_key=%s" in sql:
                    return Result(target_link)
                return Result(many=[source_link])
            if sql.startswith("select 1 from platform_review.feedback_replay_runs"):
                raise AssertionError("duplicate merge must not relocate replay link")
            if sql.startswith("update platform_review.feedback_issue_links set source_feedback_keys"):
                target_link["source_feedback_keys"] = parameters[0]
                return Result()
            if sql.startswith("update platform_review.feedback_issue_links set active=false"):
                source_link["active"] = False
                return Result()
            if sql.startswith("update platform_review.feedback_issues set disposition='duplicate'"):
                return Result({
                    **source, "disposition": "duplicate",
                    "canonical_issue_id": target_id, "row_version": 2,
                })
            if sql.startswith("insert into platform_review.feedback_issue_events"):
                self.audit_count += 1
                return Result()
            raise AssertionError(sql)

    cursor = Cursor()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return cursor

    result = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    ).merge_issue(
        source_id, target_id, expected_row_version=1,
        actor="corp:owner", reason="duplicate",
    )

    assert result["canonical_issue_id"] == target_id
    assert source_link["active"] is False
    assert replay == {
        "id": replay_id, "issue_id": source_id, "issue_link_id": source_link_id,
    }
    assert cursor.audit_count == 2


def test_every_repository_link_issue_relocation_uses_replay_conflict_guard():
    sources = "\n".join([
        inspect.getsource(PsycopgReviewRepository.move_link),
        inspect.getsource(PsycopgReviewRepository.merge_issue),
        inspect.getsource(PsycopgReviewRepository.import_release_handoff),
    ])

    assert sources.count("_assert_link_relocation_replay_free") == 3


def test_canonical_mutations_guard_cycles_inside_the_writer_transaction():
    guard = inspect.getsource(PsycopgReviewRepository._assert_canonical_edge_acyclic)
    merge = inspect.getsource(PsycopgReviewRepository.merge_issue)
    disposition = inspect.getsource(PsycopgReviewRepository.set_disposition)

    normalized = " ".join(guard.lower().split())
    assert "pg_advisory_xact_lock" in normalized
    assert "with recursive canonical_walk" in normalized
    assert "for update" in normalized
    assert "current_id=%s" in normalized
    assert "canonical cycle" in normalized
    assert "_assert_canonical_edge_acyclic" in merge
    assert "_assert_canonical_edge_acyclic" in disposition
    assert merge.index("_assert_canonical_edge_acyclic") < merge.index("set disposition='duplicate'")
    assert disposition.index("_assert_canonical_edge_acyclic") < disposition.index("set disposition=%s")


@pytest.mark.parametrize("operation", ["merge", "duplicate"])
def test_canonical_cycle_rejection_precedes_every_state_or_audit_write(operation):
    source_id, target_id = UUID(int=901), UUID(int=902)
    source = {"id": source_id, "agent_id": "agent", "row_version": 1}
    target = {"id": target_id, "agent_id": "agent", "row_version": 1}

    class Result:
        def __init__(self, row=None, many=None):
            self.row = row
            self.many = many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        mutations = []
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, _parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "agent"})
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("with recursive canonical_walk"):
                return Result({"cycle": True})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[source, target])
            if normalized.startswith(("update ", "insert ", "delete ")):
                self.mutations.append(normalized)
            raise AssertionError(normalized)

    cursor = Cursor()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return cursor

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(InvalidReviewMutation, match="cycle"):
        if operation == "merge":
            repository.merge_issue(
                source_id, target_id, expected_row_version=1,
                actor="corp:owner", reason="inverse merge",
            )
        else:
            repository.set_disposition(
                source_id, disposition="duplicate", canonical_issue_id=target_id,
                owner=None, expected_row_version=1, actor="corp:owner",
                disposition_reason="inverse duplicate",
            )

    assert cursor.mutations == []


def test_canonical_guard_rejects_a_longer_reachable_path_and_keeps_valid_edge():
    source_id, target_id, middle_id, leaf_id = (
        UUID(int=911), UUID(int=912), UUID(int=913), UUID(int=914)
    )
    graph = {source_id: None, target_id: middle_id, middle_id: source_id, leaf_id: None}
    rows = {
        issue_id: {"id": issue_id, "agent_id": "agent", "row_version": 1}
        for issue_id in graph
    }

    class Result:
        def __init__(self, row=None, many=None):
            self.row = row
            self.many = many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "agent"})
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("with recursive canonical_walk"):
                current, wanted = parameters
                visited = set()
                cycle = False
                while current is not None:
                    if current == wanted or current in visited:
                        cycle = True
                        break
                    visited.add(current)
                    current = graph[current]
                return Result({"cycle": cycle})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[rows[issue_id] for issue_id in parameters[0]])
            raise AssertionError(normalized)

    with pytest.raises(InvalidReviewMutation, match="cycle"):
        PsycopgReviewRepository._assert_canonical_edge_acyclic(
            Cursor(), source_id, target_id
        )
    valid_source, valid_target = PsycopgReviewRepository._assert_canonical_edge_acyclic(
        Cursor(), source_id, leaf_id
    )
    assert (valid_source["id"], valid_target["id"]) == (source_id, leaf_id)


def test_concurrent_inverse_canonical_edges_are_serialized_and_only_one_commits():
    left_id, right_id = UUID(int=921), UUID(int=922)
    graph = {left_id: None, right_id: None}
    agent_lock = threading.Lock()
    start = threading.Barrier(2)
    results = []

    class Result:
        def __init__(self, row=None, many=None):
            self.row = row
            self.many = many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __init__(self): self.locked = False
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "agent"})
            if "pg_advisory_xact_lock" in normalized:
                agent_lock.acquire()
                self.locked = True
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("with recursive canonical_walk"):
                current, wanted = parameters
                seen = set()
                cycle = False
                while current is not None:
                    if current == wanted or current in seen:
                        cycle = True
                        break
                    seen.add(current)
                    current = graph[current]
                return Result({"cycle": cycle})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[
                    {"id": issue_id, "agent_id": "agent", "row_version": 1}
                    for issue_id in parameters[0]
                ])
            raise AssertionError(normalized)

    def add_edge(source_id, target_id):
        cursor = Cursor()
        start.wait()
        try:
            PsycopgReviewRepository._assert_canonical_edge_acyclic(
                cursor, source_id, target_id
            )
            graph[source_id] = target_id
            results.append("committed")
        except InvalidReviewMutation:
            results.append("rejected")
        finally:
            if cursor.locked:
                agent_lock.release()

    threads = [
        threading.Thread(target=add_edge, args=(left_id, right_id)),
        threading.Thread(target=add_edge, args=(right_id, left_id)),
    ]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(results) == ["committed", "rejected"]
    assert sum(target is not None for target in graph.values()) == 1


def test_move_writer_rejects_a_link_that_no_longer_belongs_to_expected_source():
    source_id, other_id, target_id, link_id = (
        UUID(int=801), UUID(int=802), UUID(int=803), UUID(int=804)
    )
    link = {
        "id": link_id,
        "issue_id": other_id,
        "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn",
        "source_feedback_keys": [],
        "active": True,
    }

    class Result:
        def fetchone(self):
            return link

    class Cursor:
        mutated = False

        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, _parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                assert "for update" in normalized
                return Result()
            if normalized.startswith(("update ", "insert ")):
                self.mutated = True
            raise AssertionError(normalized)

    cursor = Cursor()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return cursor

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(InvalidReviewMutation, match="source issue"):
        repository.move_link(
            source_id,
            link_id,
            target_id,
            actor="corp:owner",
            reason="stale move",
        )

    assert cursor.mutated is False


def test_replay_insert_rechecks_the_locked_link_ownership_token():
    source = inspect.getsource(PsycopgReviewRepository.create_or_get_replay)
    normalized = " ".join(source.lower().split())

    assert "expected_ownership" in source
    assert "where id=%s and active for update" in normalized
    assert "replay link ownership changed" in normalized
    assert source.index("expected_ownership") < source.index("insert into platform_review.feedback_replay_runs")


def test_feedback_metadata_lookup_is_one_bounded_authoritative_query():
    statements = []

    class Result:
        def fetchone(self):
            return {"turn_key": "fae:turn", "feedback_keys": ["fb:1", "fb:2"]}

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            statements.append((" ".join(statement.lower().split()), parameters))
            return Result()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    result = repository.feedback_keys_for_turn("ai-fae-agent", "fae:turn")

    assert result == {"turn_key": "fae:turn", "feedback_keys": ["fb:1", "fb:2"]}
    assert len(statements) == 1
    assert "platform_read.turns" in statements[0][0]
    assert "platform_read.feedback" in statements[0][0]
    assert "feedback.agent_id=turn.agent_id" in statements[0][0]
    assert "feedback.turn_key=turn.turn_key" in statements[0][0]
    assert statements[0][1] == ("ai-fae-agent", "fae:turn")


def test_issue_collections_use_batched_progress_and_constant_query_count():
    list_source = inspect.getsource(PsycopgReviewRepository.list_issue_page)
    overview_source = inspect.getsource(PsycopgReviewRepository.overview)

    assert "_load_issue_detail" not in list_source
    assert "_load_issue_detail" not in overview_source
    assert "count(*) over()" in " ".join(list_source.lower().split())
    assert "latest_progress" in list_source
    assert "latest_progress" in overview_source
    assert list_source.count("cursor.execute(") == 1
    assert overview_source.count("cursor.execute(") <= 2


@pytest.mark.parametrize("population", [0, 1, 200, 501])
def test_issue_page_query_budget_is_constant_for_empty_one_page_and_many(population):
    calls = []
    page_size = min(population, 200)
    rows = [
        {
            "id": UUID(int=10_000 + index),
            "updated_at": "2026-09-01T00:00:00Z",
            "progress_status": "pending_triage",
            "progress_missing_gates": [],
        }
        for index in range(page_size)
    ]

    class Result:
        def fetchone(self):
            return {"items": rows, "total_count": population}

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            calls.append((" ".join(statement.lower().split()), parameters))
            return Result()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )
    page = repository.list_issue_page(limit=200, offset=0)

    assert len(calls) == 1
    assert len(page["items"]) == page_size
    assert page["total"] == population
    assert page["has_more"] is (population > 200)
    assert "limit %s offset %s" in calls[0][0]


def test_relocation_replay_preflights_are_metadata_only_and_server_derived():
    statements = []
    results = iter([{"conflict": True}, {"conflict": False}])

    class Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            statements.append((" ".join(statement.lower().split()), parameters))
            return Result(next(results))

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )
    source_id, target_id, link_id = UUID(int=70), UUID(int=71), UUID(int=72)

    assert repository.move_link_has_replay(source_id, link_id) is True
    assert repository.merge_relocation_has_replay(source_id, target_id) is False
    assert all("select exists" in statement for statement, _ in statements)
    assert all("feedback_replay_runs" in statement for statement, _ in statements)
    assert "not exists" in statements[1][0]
    assert all(
        keyword not in statement
        for statement, _ in statements
        for keyword in ("insert ", "update ", "delete ")
    )
