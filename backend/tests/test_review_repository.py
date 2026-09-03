from uuid import UUID
import ast
import inspect
import threading
import textwrap
from types import SimpleNamespace

import pytest

from app.review.repository import (
    ISSUE_UPDATE_FIELDS,
    ConcurrentUpdate,
    InvalidReviewMutation,
    PsycopgReviewRepository,
    require_row_version,
)
from app.review.scope_sql import (
    CANONICAL_EVENT_PAIR_INVALID_SQL,
    HISTORICAL_LINK_EVENT_INVALID_SQL,
)
from app.review.service import ReviewService


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


def test_every_canonical_edge_writer_uses_one_authoritative_helper():
    repository_source = inspect.getsource(PsycopgReviewRepository)
    helper_method = getattr(PsycopgReviewRepository, "_mutate_canonical_edge", None)

    assert helper_method is not None
    assert not hasattr(PsycopgReviewRepository, "_create_canonical_edge")
    helper = inspect.getsource(helper_method)
    assert helper.index("_assert_canonical_edge_acyclic") < helper.index(
        "before_write(source, target)"
    )
    assert repository_source.count("canonical_issue_id=%s") == 1
    assert helper.count('event_type="issue_merged"') == 1
    assert helper.count('event_type="issue_absorbed"') == 1

    tree = ast.parse(textwrap.dedent(repository_source))
    call_sites = {}
    for method in tree.body[0].body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_mutate_canonical_edge"
        ]
        if calls:
            call_sites[method.name] = len(calls)
    assert call_sites == {
        "set_disposition": 1,
        "merge_issue": 1,
        "import_release_handoff": 1,
    }


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


def test_issue_scope_sql_rejects_unpaired_or_malformed_canonical_events():
    method_source = " ".join(
        inspect.getsource(PsycopgReviewRepository.agent_issue_scope_valid)
        .lower()
        .split()
    )
    predicate = " ".join(CANONICAL_EVENT_PAIR_INVALID_SQL.lower().split())

    assert "canonical_event_pair_invalid_sql" in method_source
    assert "canonical_source.event_type='issue_merged'" in predicate
    assert "canonical_target.event_type='issue_absorbed'" in predicate
    assert "canonical_source.before->>'source_issue_id'" not in predicate
    assert "canonical_target.before->>'source_issue_id'" in predicate
    assert "canonical_source.after->>'canonical_issue_id'" in predicate
    assert "canonical_target.after->>'target_issue_id'" in predicate
    assert "canonical_target.actor is not distinct from canonical_event.actor" in predicate
    assert "canonical_source.actor is not distinct from canonical_event.actor" in predicate
    assert "canonical_target.reason is not distinct from canonical_event.reason" in predicate
    assert "canonical_source.reason is not distinct from canonical_event.reason" in predicate


def test_issue_scope_requires_current_canonical_row_to_have_exact_matching_pair():
    predicate = " ".join(CANONICAL_EVENT_PAIR_INVALID_SQL.lower().split())

    assert "issue.canonical_issue_id is not null and not exists" in predicate
    assert "canonical_source.issue_id=issue.id" in predicate
    assert (
        "canonical_source.after->>'canonical_issue_id'="
        "issue.canonical_issue_id::text"
    ) in predicate
    assert "canonical_target.issue_id=issue.canonical_issue_id" in predicate
    assert "canonical_target.before->>'source_issue_id'=issue.id::text" in predicate
    assert (
        "canonical_target.after->>'target_issue_id'="
        "issue.canonical_issue_id::text"
    ) in predicate
    assert "canonical_target.actor is not distinct from canonical_source.actor" in predicate
    assert "canonical_target.reason is not distinct from canonical_source.reason" in predicate


def test_all_canonical_merge_events_are_inside_the_only_edge_mutation_helper():
    repository_source = inspect.getsource(PsycopgReviewRepository)
    helper_method = getattr(PsycopgReviewRepository, "_mutate_canonical_edge", None)

    assert helper_method is not None
    helper = inspect.getsource(helper_method)
    assert repository_source.count('event_type="issue_merged"') == 1
    assert repository_source.count('event_type="issue_absorbed"') == 1
    assert 'event_type="issue_merged"' in helper
    assert 'event_type="issue_absorbed"' in helper


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


def test_feedback_scope_accepts_additive_evolution_but_rejects_missing_and_duplicate_keys():
    current = " ".join(
        inspect.getsource(PsycopgReviewRepository.agent_issue_scope_valid)
        .lower().split()
    )
    historical = " ".join(HISTORICAL_LINK_EVENT_INVALID_SQL.lower().split())

    assert "not (linked_feedback.feedback_key = any(link.source_feedback_keys))" not in current
    assert "count(distinct feedback_key)" in current
    assert "not exists" in current and "linked_feedback.feedback_key is null" in current
    assert "not exists ( select 1 from platform_read.feedback event_feedback" not in historical
    assert "count(distinct feedback_key)" in historical


def test_handoff_and_backfill_collect_all_feedback_for_negative_turns():
    handoff = " ".join(
        inspect.getsource(PsycopgReviewRepository.import_release_handoff)
        .lower().split()
    )
    backfill = " ".join(
        inspect.getsource(PsycopgReviewRepository.list_negative_feedback_groups)
        .lower().split()
    )

    assert "bool_or(feedback.sentiment='negative')" in handoff
    assert "filter (where feedback.sentiment='negative')" not in handoff
    assert "negative_turns" in backfill
    assert "join platform_read.feedback all_feedback" in backfill
    assert "all_turns as materialized" in backfill
    assert "left join all_turns t" in backfill


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
    lock = inspect.getsource(PsycopgReviewRepository._lock_canonical_agent)
    endpoints = inspect.getsource(PsycopgReviewRepository._lock_canonical_endpoints)
    guard = inspect.getsource(PsycopgReviewRepository._assert_canonical_edge_acyclic)
    helper = inspect.getsource(PsycopgReviewRepository._mutate_canonical_edge)
    merge = inspect.getsource(PsycopgReviewRepository.merge_issue)
    disposition = inspect.getsource(PsycopgReviewRepository.set_disposition)

    normalized = " ".join(guard.lower().split())
    assert "pg_advisory_xact_lock" in lock
    assert "_lock_canonical_agent" in endpoints
    assert "_lock_canonical_endpoints" in guard
    assert "with recursive canonical_walk" in normalized
    assert "for update" in endpoints.lower()
    assert "current_id=%s" in normalized
    assert "canonical cycle" in normalized
    assert "_mutate_canonical_edge" in merge
    assert "_mutate_canonical_edge" in disposition
    assert helper.index("_assert_canonical_edge_acyclic") < helper.index("set disposition='duplicate'")


@pytest.mark.asyncio
async def test_actual_generic_duplicate_disposition_emits_pair_once_and_is_idempotent():
    source_id, target_id = UUID(int=881), UUID(int=882)
    issues = {
        source_id: {
            "id": source_id,
            "agent_id": "generic-agent",
            "row_version": 1,
            "disposition": "actionable",
            "canonical_issue_id": None,
        },
        target_id: {
            "id": target_id,
            "agent_id": "generic-agent",
            "row_version": 1,
            "disposition": "actionable",
            "canonical_issue_id": None,
        },
    }
    events = {source_id: [], target_id: []}
    update_count = 0

    def canonical_scope_valid() -> bool:
        sources = [
            event for event in events[source_id]
            if event["event_type"] == "issue_merged"
            and event["after"].get("canonical_issue_id") == str(target_id)
        ]
        targets = [
            event for event in events[target_id]
            if event["event_type"] == "issue_absorbed"
            and event["before"].get("source_issue_id") == str(source_id)
            and event["after"].get("target_issue_id") == str(target_id)
        ]
        return any(
            target["actor"] == source["actor"]
            and target["reason"] == source["reason"]
            for source in sources for target in targets
        )

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters=()):
            nonlocal update_count
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": issues[parameters[0]]["agent_id"]})
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[dict(issues[issue_id]) for issue_id in parameters[0]])
            if normalized.startswith("with recursive canonical_walk") and "as valid" in normalized:
                assert "issue.canonical_issue_id is not null and not exists" in normalized
                return Result({"valid": canonical_scope_valid()})
            if normalized.startswith("with recursive canonical_walk"):
                return Result({"cycle": False})
            if normalized.startswith("update platform_review.feedback_issues set disposition='duplicate'"):
                canonical_id, owner, reason, selected_source_id = parameters
                assert owner == "corp:owner"
                update_count += 1
                issues[selected_source_id].update({
                    "disposition": "duplicate",
                    "canonical_issue_id": canonical_id,
                    "disposition_reason": reason,
                    "row_version": issues[selected_source_id]["row_version"] + 1,
                })
                return Result(dict(issues[selected_source_id]))
            if normalized.startswith("insert into platform_review.feedback_issue_events"):
                issue_id, event_type, actor, reason, before, after = parameters
                events[issue_id].append({
                    "event_type": event_type,
                    "actor": actor,
                    "reason": reason,
                    "before": before.obj,
                    "after": after.obj,
                })
                return Result()
            raise AssertionError(normalized)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )
    repository.recalculate_and_record_transition = lambda *_args, **_kwargs: None
    repository.get_issue_detail = lambda issue_id: {"issue": dict(issues[issue_id])}
    service = ReviewService(repository, write_repository=repository)
    payload = SimpleNamespace(
        disposition="duplicate",
        canonical_issue_id=target_id,
        owner="corp:owner",
        row_version=1,
        reason="same root cause",
    )

    await service.set_disposition(source_id, payload, actor="corp:owner")
    payload.row_version = 2
    await service.set_disposition(source_id, payload, actor="corp:owner")

    assert update_count == 1
    assert [event["event_type"] for event in events[source_id]] == [
        "issue_merged", "issue_disposition_set"
    ]
    assert [event["event_type"] for event in events[target_id]] == ["issue_absorbed"]
    merged, disposition = events[source_id]
    absorbed = events[target_id][0]
    assert merged["after"]["canonical_issue_id"] == str(target_id)
    assert absorbed["before"] == {"source_issue_id": str(source_id)}
    assert absorbed["after"] == {"target_issue_id": str(target_id)}
    assert {merged["actor"], absorbed["actor"], disposition["actor"]} == {"corp:owner"}
    assert {merged["reason"], absorbed["reason"], disposition["reason"]} == {
        "same root cause"
    }
    assert repository.agent_issue_scope_valid("generic-agent") is True

    events[target_id].clear()
    assert repository.agent_issue_scope_valid("generic-agent") is False
    mismatched = dict(absorbed)
    mismatched["actor"] = "corp:different"
    events[target_id].append(mismatched)
    assert repository.agent_issue_scope_valid("generic-agent") is False


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


def test_concurrent_guarded_mutation_and_handoff_style_edge_share_serialization():
    left_id, right_id = UUID(int=923), UUID(int=924)
    graph = {left_id: None, right_id: None}
    lock = threading.Lock()
    start = threading.Barrier(2)
    outcomes = []

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __init__(self): self.locked = False
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if "pg_advisory_xact_lock" in normalized:
                lock.acquire()
                self.locked = True
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[{
                    "id": issue_id,
                    "agent_id": "ai-fae-agent",
                    "row_version": 1,
                    "disposition": "actionable",
                } for issue_id in parameters[0]])
            if normalized.startswith("with recursive canonical_walk"):
                current, wanted = parameters
                seen = set()
                while current is not None and current not in seen:
                    if current == wanted:
                        return Result({"cycle": True})
                    seen.add(current)
                    current = graph[current]
                return Result({"cycle": current in seen})
            if normalized.startswith("update platform_review.feedback_issues"):
                target_id, _owner, _reason, source_id = parameters
                graph[source_id] = target_id
                return Result({
                    "id": source_id,
                    "agent_id": "ai-fae-agent",
                    "row_version": 2,
                    "disposition": "duplicate",
                    "canonical_issue_id": target_id,
                })
            if normalized.startswith("insert into platform_review.feedback_issue_events"):
                return Result()
            raise AssertionError(normalized)

    def create_edge(label, source_id, target_id, *, handoff_style):
        cursor = Cursor()
        start.wait()
        relocated = []
        try:
            PsycopgReviewRepository._mutate_canonical_edge(
                cursor,
                source_id,
                target_id,
                disposition_reason=label,
                actor=label,
                reason=label,
                **({"before_write": lambda *_args: relocated.append(label)} if handoff_style else {}),
            )
            outcomes.append((label, "committed", bool(relocated)))
        except InvalidReviewMutation:
            outcomes.append((label, "rejected", bool(relocated)))
        finally:
            if cursor.locked:
                lock.release()

    threads = [
        threading.Thread(
            target=create_edge,
            args=("ordinary", left_id, right_id),
            kwargs={"handoff_style": False},
        ),
        threading.Thread(
            target=create_edge,
            args=("handoff", right_id, left_id),
            kwargs={"handoff_style": True},
        ),
    ]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(state for _label, state, _relocated in outcomes) == ["committed", "rejected"]
    assert all(not relocated for _label, state, relocated in outcomes if state == "rejected")
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


def test_create_writer_rejects_foreign_origin_before_issue_or_audit_write():
    statements = []

    class Result:
        def fetchone(self): return None

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            statements.append((normalized, parameters))
            if normalized.startswith("select turn.turn_key"):
                return Result()
            raise AssertionError("foreign origin must be rejected before a write")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(InvalidReviewMutation, match="origin turn"):
        repository.create_issue(
            {
                "agent_id": "ai-fae-agent",
                "origin_turn_key": "admin:turn",
                "title": "foreign origin",
            },
            actor="codex",
            reason="reject foreign source",
        )

    assert len(statements) == 1
    assert statements[0][1] == ("ai-fae-agent", "admin:turn")


@pytest.mark.parametrize(
    ("issue_agent", "source_agent", "metadata", "provided", "message"),
    [
        ("ai-fae-agent", "admin-agent", None, [], "target issue"),
        ("ai-fae-agent", "ai-fae-agent", None, [], "feedback lineage"),
        (
            "ai-fae-agent",
            "ai-fae-agent",
            {"turn_key": "fae:turn", "feedback_keys": ["fae:real"]},
            ["fae:foreign"],
            "feedback lineage",
        ),
    ],
)
def test_link_writer_locks_target_and_rejects_cross_scope_before_write(
    issue_agent, source_agent, metadata, provided, message
):
    issue_id = UUID(int=1401)
    statements = []

    class Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            statements.append((normalized, parameters))
            if normalized.startswith("select * from platform_review.feedback_issues"):
                assert "for update" in normalized
                return Result({"id": issue_id, "agent_id": issue_agent})
            if normalized.startswith("select turn.turn_key"):
                return Result(metadata)
            raise AssertionError("invalid link must be rejected before a write")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(InvalidReviewMutation, match=message):
        repository.link_turn(
            issue_id,
            agent_id=source_agent,
            source_turn_key="fae:turn",
            source_feedback_keys=provided,
            link_role="primary",
            actor="codex",
            reason="scope check",
        )

    assert statements[0][1] == (issue_id,)
    assert not any(sql.startswith(("insert ", "update ", "delete ")) for sql, _ in statements)


@pytest.mark.parametrize("path_length", [1, 2])
def test_actual_release_handoff_cycle_rolls_back_link_issue_and_audit_state(path_length):
    source_id, target_id, middle_id, link_id = (
        UUID(int=1201), UUID(int=1202), UUID(int=1203), UUID(int=1204)
    )
    source_issue = {
        "id": source_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
    }
    target_issue = {
        "id": target_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
        "root_cause": "known",
        "owner": "corp:owner",
    }
    link = {
        "id": link_id,
        "issue_id": source_id,
        "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn",
        "source_feedback_keys": ["fae:negative"],
        "active": True,
    }
    graph = {
        source_id: None,
        target_id: source_id if path_length == 1 else middle_id,
        middle_id: source_id,
    }
    attempts = []
    committed = []

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select * from platform_review.feedback_release_handoffs"):
                return Result(None)
            if normalized.startswith("insert into platform_review.feedback_release_handoffs"):
                attempts.append("handoff")
                return Result()
            if normalized.startswith("insert into platform_review.feedback_release_handoff_events"):
                attempts.append("handoff_audit")
                return Result()
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select turn.agent_id, turn.turn_key"):
                return Result(many=[{
                    "agent_id": "ai-fae-agent",
                    "turn_key": "fae:turn",
                    "feedback_keys": ["fae:negative", "fae:positive"],
                    "has_negative_feedback": True,
                }])
            if "where agent_id=%s and canonical_key=%s" in normalized:
                return Result(target_issue)
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                return Result(link)
            if normalized.startswith("select 1 from platform_review.feedback_issue_links"):
                return Result(None)
            if "as target_issue_id" in normalized and "event_type='link_moved_out'" in normalized:
                return Result(many=[])
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[source_issue, target_issue])
            if normalized.startswith("with recursive canonical_walk"):
                current, wanted = parameters
                seen = set()
                while current is not None and current not in seen:
                    if current == wanted:
                        return Result({"cycle": True})
                    seen.add(current)
                    current = graph[current]
                return Result({"cycle": current in seen})
            if normalized.startswith(("insert ", "update ", "delete ")):
                attempts.append(normalized)
            raise AssertionError(normalized)

    cursor = Cursor()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, exc_type, *_args):
            if exc_type is None:
                committed.extend(attempts)
            else:
                attempts.clear()
            return None
        def cursor(self): return cursor

    validated = SimpleNamespace(
        idempotency_key="sha256:" + "a" * 64,
        batch_id="batch-cycle",
        agent_id="ai-fae-agent",
        payload_sha256="b" * 64,
        release_name="release-1",
        deployment_sha="c" * 40,
        remediation_commit="d" * 40,
        release_manifest_ref="release.json",
        repository_path="/repo",
        merge_verification={},
        deployment_verification={},
        issues=(SimpleNamespace(
            issue_key="target",
            title="target",
            failure_layer="synthesis",
            secondary_layers=(),
            expected_repair="repair",
        ),),
        items=(SimpleNamespace(turn_key="fae:turn", issue_key="target"),),
    )

    with pytest.raises(InvalidReviewMutation, match="cycle"):
        PsycopgReviewRepository(
            "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
        ).import_release_handoff(validated)

    assert committed == []
    assert attempts == []
    assert link["issue_id"] == source_id


def test_actual_valid_release_handoff_moves_link_and_canonicalizes_with_all_feedback():
    source_id, target_id, link_id = UUID(int=1251), UUID(int=1252), UUID(int=1253)
    source_issue = {
        "id": source_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
    }
    target_issue = {
        "id": target_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
        "root_cause": "known",
        "owner": "corp:owner",
        "fix_ready_at": "2026-09-01T00:00:00Z",
    }
    link = {
        "id": link_id,
        "issue_id": source_id,
        "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn",
        "source_feedback_keys": ["fae:negative"],
        "active": True,
    }
    issue_events = []
    canonical_events = []
    evidence_sequence = iter([UUID(int=1254), UUID(int=1255)])
    handoff_row = {
        "idempotency_key": "sha256:" + "a" * 64,
        "batch_id": "batch-valid",
        "agent_id": "ai-fae-agent",
        "payload_sha256": "b" * 64,
        "release_name": "release-1",
        "deployment_sha": "c" * 40,
        "import_status": "processing",
        "result": None,
    }

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select * from platform_review.feedback_release_handoffs"):
                if " or batch_id=%s" in normalized:
                    return Result(None)
                return Result(handoff_row)
            if normalized.startswith("insert into platform_review.feedback_release_handoffs"):
                return Result()
            if normalized.startswith("insert into platform_review.feedback_release_handoff_events"):
                return Result()
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select turn.agent_id, turn.turn_key"):
                return Result(many=[{
                    "agent_id": "ai-fae-agent",
                    "turn_key": "fae:turn",
                    "feedback_keys": ["fae:negative", "fae:positive"],
                    "has_negative_feedback": True,
                }])
            if "where agent_id=%s and canonical_key=%s" in normalized:
                return Result(target_issue)
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                return Result(link)
            if normalized.startswith("select 1 from platform_review.feedback_issue_links"):
                return Result(None)
            if "as target_issue_id" in normalized and "event_type='link_moved_out'" in normalized:
                return Result(many=[])
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[source_issue, target_issue])
            if normalized.startswith("with recursive canonical_walk") and "as valid" in normalized:
                merged = next(
                    (event for event in canonical_events if event[1] == "issue_merged"),
                    None,
                )
                absorbed = next(
                    (event for event in canonical_events if event[1] == "issue_absorbed"),
                    None,
                )
                return Result({
                    "valid": bool(
                        merged
                        and absorbed
                        and merged[3]["canonical_issue_id"] == str(target_id)
                        and absorbed[2]["source_issue_id"] == str(source_id)
                        and absorbed[3]["target_issue_id"] == str(target_id)
                    )
                })
            if normalized.startswith("with recursive canonical_walk"):
                return Result({"cycle": False})
            if normalized.startswith("select 1 from platform_review.feedback_replay_runs"):
                return Result(None)
            if normalized.startswith("update platform_review.feedback_issue_links"):
                target, keys, _actor, _reason, selected_link_id = parameters
                assert selected_link_id == link_id
                link.update({"issue_id": target, "source_feedback_keys": keys})
                return Result(dict(link))
            if normalized.startswith("update platform_review.feedback_issues set disposition='duplicate'"):
                canonical_id, _owner, reason, selected_source_id = parameters
                assert selected_source_id == source_id
                source_issue.update({
                    "canonical_issue_id": canonical_id,
                    "disposition": "duplicate",
                    "disposition_reason": reason,
                    "row_version": 2,
                })
                return Result(dict(source_issue))
            if normalized.startswith("insert into platform_review.feedback_issue_events"):
                issue_events.append((parameters[0], parameters[1]))
                if parameters[1] in ("issue_merged", "issue_absorbed"):
                    canonical_events.append(
                        (parameters[0], parameters[1], parameters[4].obj, parameters[5].obj)
                    )
                return Result()
            if normalized.startswith("select * from platform_review.feedback_issues"):
                return Result(target_issue)
            if normalized.startswith("insert into platform_review.feedback_fix_evidence"):
                return Result({"id": next(evidence_sequence), "issue_id": target_id})
            if normalized.startswith("update platform_review.feedback_release_handoffs"):
                return Result()
            raise AssertionError(normalized)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    validated = SimpleNamespace(
        idempotency_key=handoff_row["idempotency_key"],
        batch_id=handoff_row["batch_id"],
        agent_id="ai-fae-agent",
        payload_sha256=handoff_row["payload_sha256"],
        release_name=handoff_row["release_name"],
        deployment_sha=handoff_row["deployment_sha"],
        remediation_commit="d" * 40,
        release_manifest_ref="release.json",
        repository_path="/repo",
        merge_verification={},
        deployment_verification={},
        issues=(SimpleNamespace(
            issue_key="target",
            title="target",
            failure_layer="synthesis",
            secondary_layers=(),
            expected_repair="repair",
        ),),
        items=(SimpleNamespace(turn_key="fae:turn", issue_key="target"),),
    )
    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )
    repository._recalculate_with_cursor = lambda *_args, **_kwargs: None

    result = repository.import_release_handoff(validated)

    assert result["state"] == "imported"
    assert result["issue_ids"] == [str(target_id)]
    assert len(result["evidence_ids"]) == 2
    assert link["issue_id"] == target_id
    assert link["source_feedback_keys"] == ["fae:negative", "fae:positive"]
    assert source_issue["canonical_issue_id"] == target_id
    assert (source_id, "issue_merged") in issue_events
    assert (target_id, "issue_absorbed") in issue_events
    assert (source_id, "link_moved_out") in issue_events
    assert (target_id, "link_moved_in") in issue_events
    assert repository.agent_issue_scope_valid("ai-fae-agent") is True


@pytest.mark.parametrize("reverse_items", [False, True])
def test_actual_split_release_handoff_preserves_moves_without_false_canonical_edge(
    reverse_items,
):
    source_id, target_b_id, target_c_id = UUID(int=1271), UUID(int=1272), UUID(int=1273)
    link_b_id, link_c_id = UUID(int=1274), UUID(int=1275)
    source_issue = {
        "id": source_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
        "canonical_issue_id": None,
    }
    targets = {
        "target-b": {
            "id": target_b_id,
            "agent_id": "ai-fae-agent",
            "row_version": 1,
            "disposition": "actionable",
            "root_cause": "known",
            "owner": "corp:owner",
            "fix_ready_at": "2026-09-01T00:00:00Z",
        },
        "target-c": {
            "id": target_c_id,
            "agent_id": "ai-fae-agent",
            "row_version": 1,
            "disposition": "actionable",
            "root_cause": "known",
            "owner": "corp:owner",
            "fix_ready_at": "2026-09-01T00:00:00Z",
        },
    }
    links = {
        "fae:turn-b": {
            "id": link_b_id,
            "issue_id": source_id,
            "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-b",
            "source_feedback_keys": ["fae:feedback-b"],
            "active": True,
        },
        "fae:turn-c": {
            "id": link_c_id,
            "issue_id": source_id,
            "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-c",
            "source_feedback_keys": ["fae:feedback-c"],
            "active": True,
        },
    }
    issue_events = []
    handoff_events = []
    evidence_sequence = iter(UUID(int=value) for value in range(1276, 1280))
    handoff_row = {
        "idempotency_key": "sha256:" + "e" * 64,
        "batch_id": "batch-split",
        "agent_id": "ai-fae-agent",
        "payload_sha256": "f" * 64,
        "release_name": "release-split",
        "deployment_sha": "1" * 40,
        "import_status": None,
        "result": None,
    }

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select * from platform_review.feedback_release_handoffs"):
                if " or batch_id=%s" in normalized:
                    if handoff_row["import_status"]:
                        return Result(dict(handoff_row))
                    return Result(None)
                return Result(dict(handoff_row))
            if normalized.startswith("insert into platform_review.feedback_release_handoffs"):
                handoff_row["import_status"] = "processing"
                return Result()
            if normalized.startswith("insert into platform_review.feedback_release_handoff_events"):
                handoff_events.append(parameters[1])
                return Result()
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select turn.agent_id, turn.turn_key"):
                return Result(many=[
                    {
                        "agent_id": "ai-fae-agent",
                        "turn_key": turn_key,
                        "feedback_keys": [feedback_key],
                        "has_negative_feedback": True,
                    }
                    for turn_key, feedback_key in (
                        ("fae:turn-b", "fae:feedback-b"),
                        ("fae:turn-c", "fae:feedback-c"),
                    )
                ])
            if "where agent_id=%s and canonical_key=%s" in normalized:
                return Result(targets[parameters[1]])
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                return Result(dict(links[parameters[1]]))
            if normalized.startswith("select 1 from platform_review.feedback_issue_links"):
                source, excluded = parameters
                remaining = next(
                    (
                        link for link in links.values()
                        if link["active"] and link["issue_id"] == source and link["id"] != excluded
                    ),
                    None,
                )
                return Result({"exists": 1} if remaining else None)
            if "as target_issue_id" in normalized and "event_type='link_moved_out'" in normalized:
                moved_targets = {
                    str(after["issue_id"])
                    for issue_id, event_type, _before, after in issue_events
                    if issue_id == source_id and event_type == "link_moved_out"
                }
                return Result(many=[{"target_issue_id": value} for value in moved_targets])
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "ai-fae-agent"})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                by_id = {
                    source_id: source_issue,
                    target_b_id: targets["target-b"],
                    target_c_id: targets["target-c"],
                }
                return Result(many=[by_id[value] for value in parameters[0]])
            if normalized.startswith("with recursive canonical_walk"):
                exact_moves = all(
                    any(
                        event_type == "link_moved_out"
                        and before["id"] == str(link["id"])
                        for _issue_id, event_type, before, _after in issue_events
                    ) and any(
                        event_type == "link_moved_in"
                        and after["id"] == str(link["id"])
                        for _issue_id, event_type, _before, after in issue_events
                    )
                    for link in links.values()
                )
                paired_canonical = not any(
                    event_type in ("issue_merged", "issue_absorbed")
                    for _issue_id, event_type, _before, _after in issue_events
                )
                return Result({"valid": exact_moves and paired_canonical})
            if normalized.startswith("select 1 from platform_review.feedback_replay_runs"):
                return Result(None)
            if normalized.startswith("update platform_review.feedback_issue_links"):
                target_id, keys, _actor, _reason, selected_link_id = parameters
                selected = next(link for link in links.values() if link["id"] == selected_link_id)
                selected.update({"issue_id": target_id, "source_feedback_keys": keys})
                return Result(dict(selected))
            if normalized.startswith("update platform_review.feedback_issues set disposition='duplicate'"):
                raise AssertionError("split handoff must not create a canonical edge")
            if normalized.startswith("insert into platform_review.feedback_issue_events"):
                issue_events.append(
                    (parameters[0], parameters[1], parameters[4].obj, parameters[5].obj)
                )
                return Result()
            if normalized.startswith("select * from platform_review.feedback_issues"):
                by_id = {target_b_id: targets["target-b"], target_c_id: targets["target-c"]}
                return Result(by_id[parameters[0]])
            if normalized.startswith("insert into platform_review.feedback_fix_evidence"):
                return Result({"id": next(evidence_sequence), "issue_id": parameters[0]})
            if normalized.startswith("update platform_review.feedback_release_handoffs"):
                if "set import_status='imported'" in normalized:
                    handoff_row.update(import_status="imported", result=parameters[0].obj)
                return Result()
            raise AssertionError(normalized)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    items = [
        SimpleNamespace(turn_key="fae:turn-b", issue_key="target-b"),
        SimpleNamespace(turn_key="fae:turn-c", issue_key="target-c"),
    ]
    if reverse_items:
        items.reverse()
    validated = SimpleNamespace(
        idempotency_key=handoff_row["idempotency_key"],
        batch_id=handoff_row["batch_id"],
        agent_id="ai-fae-agent",
        payload_sha256=handoff_row["payload_sha256"],
        release_name=handoff_row["release_name"],
        deployment_sha=handoff_row["deployment_sha"],
        remediation_commit="2" * 40,
        release_manifest_ref="release.json",
        repository_path="/repo",
        merge_verification={},
        deployment_verification={},
        issues=tuple(
            SimpleNamespace(
                issue_key=key,
                title=key,
                failure_layer="synthesis",
                secondary_layers=(),
                expected_repair="repair",
            )
            for key in ("target-b", "target-c")
        ),
        items=tuple(items),
    )
    repository = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    )
    repository._recalculate_with_cursor = lambda *_args, **_kwargs: None

    first = repository.import_release_handoff(validated)
    event_count = len(issue_events)
    second = repository.import_release_handoff(validated)

    assert first == second
    assert len(issue_events) == event_count
    assert links["fae:turn-b"]["issue_id"] == target_b_id
    assert links["fae:turn-c"]["issue_id"] == target_c_id
    assert source_issue["canonical_issue_id"] is None
    assert source_issue["disposition"] == "actionable"
    assert not any(
        event_type in ("issue_merged", "issue_absorbed")
        for _issue_id, event_type, _before, _after in issue_events
    )
    assert sum(event_type == "link_moved_out" for _, event_type, _, _ in issue_events) == 2
    assert sum(event_type == "link_moved_in" for _, event_type, _, _ in issue_events) == 2
    assert repository.agent_issue_scope_valid("ai-fae-agent") is True
    assert handoff_events.count("handoff_imported") == 1


def test_release_handoff_with_remaining_link_rejects_foreign_source_issue_before_write():
    source_id, target_id, link_id = UUID(int=1261), UUID(int=1262), UUID(int=1263)
    link = {
        "id": link_id,
        "issue_id": source_id,
        "agent_id": "ai-fae-agent",
        "source_turn_key": "fae:turn",
        "source_feedback_keys": ["fae:negative"],
        "active": True,
    }
    source_issue = {
        "id": source_id,
        "agent_id": "admin-agent",
        "row_version": 1,
        "disposition": "actionable",
    }
    target_issue = {
        "id": target_id,
        "agent_id": "ai-fae-agent",
        "row_version": 1,
        "disposition": "actionable",
        "root_cause": "known",
        "owner": "corp:owner",
    }
    writes = []

    class Result:
        def __init__(self, row=None, many=None): self.row, self.many = row, many
        def fetchone(self): return self.row
        def fetchall(self): return self.many or []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select * from platform_review.feedback_release_handoffs"):
                return Result(None)
            if normalized.startswith("insert into platform_review.feedback_release_handoffs"):
                writes.append("staged handoff")
                return Result()
            if normalized.startswith("insert into platform_review.feedback_release_handoff_events"):
                writes.append("staged handoff audit")
                return Result()
            if "pg_advisory_xact_lock" in normalized:
                return Result({"pg_advisory_xact_lock": None})
            if normalized.startswith("select turn.agent_id, turn.turn_key"):
                return Result(many=[{
                    "agent_id": "ai-fae-agent",
                    "turn_key": "fae:turn",
                    "feedback_keys": ["fae:negative"],
                    "has_negative_feedback": True,
                }])
            if "where agent_id=%s and canonical_key=%s" in normalized:
                return Result(target_issue)
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                return Result(link)
            if normalized.startswith("select 1 from platform_review.feedback_issue_links"):
                return Result({"?column?": 1})
            if normalized.startswith("select agent_id from platform_review.feedback_issues"):
                return Result({"agent_id": "admin-agent"})
            if normalized.startswith("select * from platform_review.feedback_issues") and "id=any" in normalized:
                return Result(many=[source_issue, target_issue])
            if normalized.startswith(("update ", "insert ", "delete ")):
                writes.append(normalized)
            raise AssertionError("foreign source must be rejected before link mutation")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args):
            writes.clear()
            return None
        def cursor(self): return Cursor()

    validated = SimpleNamespace(
        idempotency_key="sha256:" + "e" * 64,
        batch_id="batch-foreign-source",
        agent_id="ai-fae-agent",
        payload_sha256="f" * 64,
        release_name="release-1",
        deployment_sha="a" * 40,
        remediation_commit="b" * 40,
        release_manifest_ref="release.json",
        repository_path="/repo",
        merge_verification={},
        deployment_verification={},
        issues=(SimpleNamespace(
            issue_key="target",
            title="target",
            failure_layer="synthesis",
            secondary_layers=(),
            expected_repair="repair",
        ),),
        items=(SimpleNamespace(turn_key="fae:turn", issue_key="target"),),
    )

    with pytest.raises(InvalidReviewMutation, match="same agent"):
        PsycopgReviewRepository(
            "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
        ).import_release_handoff(validated)

    assert writes == []
    assert link["issue_id"] == source_id


@pytest.mark.parametrize(
    ("existing_keys", "current_keys"),
    [([], ("fae:later-negative",)),
     (["fae:negative"], ("fae:negative", "fae:later-positive"))],
)
def test_backfill_updates_current_link_with_additive_feedback_evolution(
    existing_keys, current_keys
):
    issue_id, link_id = UUID(int=1301), UUID(int=1302)
    writes = []

    class Result:
        def __init__(self, row=None): self.row = row
        def fetchone(self): return self.row

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, parameters):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("insert into platform_review.feedback_issues"):
                return Result(None)
            if normalized.startswith("select * from platform_review.feedback_issues"):
                return Result({
                    "id": issue_id,
                    "disposition": "actionable",
                    "canonical_issue_id": None,
                })
            if normalized.startswith("select * from platform_review.feedback_issue_links"):
                return Result({
                    "id": link_id,
                    "source_feedback_keys": list(existing_keys),
                })
            if normalized.startswith("update platform_review.feedback_issue_links"):
                writes.append(parameters)
                return Result(None)
            raise AssertionError(normalized)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    group = SimpleNamespace(
        agent_id="ai-fae-agent",
        turn_key="fae:turn",
        question="question",
        feedback_keys=current_keys,
    )
    result = PsycopgReviewRepository(
        "postgresql://review", connect=lambda *_args, **_kwargs: Connection()
    ).backfill_negative_group(group, actor="codex")

    assert result == (False, False, False)
    assert writes == [(sorted(set(current_keys)), link_id)]


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
