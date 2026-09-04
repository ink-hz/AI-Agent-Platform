from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from app.hr.models import (
    BindPositionConversation,
    CreateManualPosition,
    ProjectOfficialPosition,
)
from app.hr.position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
    CreatePositionTaskRequest,
    ProjectOfficialVersion,
)
from app.hr.position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceRepository,
)
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.repository import HrPositionRepository
from test_control_plane_migration import control_database


def _owner(connection, name: str):
    owner_id = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,%s,'active')",
        (owner_id, name),
    )
    connection.commit()
    return owner_id


@pytest.mark.postgres
def test_context_confirmation_is_replay_stable_and_rejects_a_stale_baseline(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Position Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "结构工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    first_draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"mission": {"text": "Build reliable robots"}}, "Initial context", uuid4(),
    ))
    replay_draft_command = CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"jr": {"skills": ["CAD"]}}, "Stable draft", uuid4(),
    )
    repository.create_draft(replay_draft_command)
    with pytest.raises(PositionContextConflict):
        repository.create_draft(replace(
            replay_draft_command, summary="changed replay"
        ))
    first_command = ConfirmContextModules(
        owner_id, position.position_id, first_draft.context_version_id, uuid4(),
        None, first_draft.row_version, ("mission",), owner_id,
    )

    first = repository.confirm_modules(first_command)
    assert repository.confirm_modules(first_command) == first
    with pytest.raises(PositionContextConflict):
        repository.confirm_modules(replace(
            first_command, expected_draft_row_version=999
        ))
    with pytest.raises(PositionContextConflict):
        repository.confirm_modules(replace(
            first_command, draft_context_version_id=uuid4()
        ))
    second_draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, first.context_version_id, None,
        {"jd": {"duty": "Design structures"}}, "Second context", uuid4(),
    ))
    with pytest.raises(PositionContextConflict):
        repository.confirm_modules(ConfirmContextModules(
            owner_id, position.position_id, second_draft.context_version_id,
            uuid4(), None, second_draft.row_version, ("jd",), owner_id,
        ))

    current = repository.current(owner_id, position.position_id)
    assert current == first
    assert first.state == "confirmed"


@pytest.mark.postgres
def test_repository_keeps_partial_draft_and_compares_immutable_versions(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Partial Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "算法工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"mission": {"text": "Perception"}, "unknowns": {"items": ["team size"]}},
        "Proposed context", uuid4(),
    ))
    confirmed = repository.confirm_modules(ConfirmContextModules(
        owner_id, position.position_id, draft.context_version_id, uuid4(), None,
        draft.row_version, ("mission",), owner_id,
    ))

    drafts = repository.list_versions(owner_id, position.position_id, state="draft")
    assert drafts[0].modules == {"unknowns": {"items": ("team size",)}}
    assert drafts[0].row_version == 2
    comparison = repository.compare(
        owner_id, position.position_id, confirmed.context_version_id,
        drafts[0].context_version_id,
    )
    assert comparison["changed_modules"] == ("mission", "unknowns")
    assert comparison["left"]["mission"] == {"text": "Perception"}


@pytest.mark.postgres
def test_repository_conceals_cross_owner_contexts(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Visible Context Owner")
        other_id = _owner(admin, "Hidden Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "光学工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"jr": {"skills": ["optics"]}}, "JR", uuid4(),
    ))

    assert repository.current(other_id, position.position_id) is None
    assert repository.list_versions(other_id, position.position_id) == ()
    with pytest.raises(PositionContextNotFound):
        repository.compare(
            other_id, position.position_id, draft.context_version_id,
            draft.context_version_id,
        )


@pytest.mark.postgres
def test_official_version_replay_rejects_any_payload_change(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Official Version Replay Owner")
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position_id = uuid4()
    positions.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "算法工程师", "机器人",
        ("深圳",), "active", "sync-v1", "a" * 64,
        datetime(2026, 9, 4, tzinfo=UTC),
    ))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    now = datetime(2026, 9, 4, tzinfo=UTC)
    command = ProjectOfficialVersion(
        uuid4(), owner_id, position_id, uuid4(), "J11014", "算法工程师",
        "机器人", ("深圳",), "研发", "算法", 1, "本科", "全职",
        "20K-30K", "Build.", "Test.", "sync-v1", now, "a" * 64,
        now, now, "active", "published", {"snapshot": "sync-v1"},
    )

    first = repository.project_official_version(command)
    assert repository.project_official_version(command) == first
    assert repository.official_version(
        owner_id, position_id, first.official_position_version_id
    ) == first
    with pytest.raises(PositionContextNotFound):
        repository.official_version(
            uuid4(), position_id, first.official_position_version_id
        )
    with pytest.raises(PositionContextConflict):
        repository.project_official_version(replace(command, duty="Changed."))


@pytest.mark.postgres
def test_official_versions_are_append_only_and_older_observation_cannot_rollback_current(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Official Append Only Owner")
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position_id = uuid4()
    positions.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11015", "平台工程师", "研发",
        ("上海",), "active", "sync-v1", "b" * 64,
        datetime(2026, 9, 4, tzinfo=UTC),
    ))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    base = ProjectOfficialVersion(
        uuid4(), owner_id, position_id, uuid4(), "J11015", "平台工程师",
        "研发", ("上海",), "技术", "平台", 0, "本科", "全职", "面议",
        "Build.", "Operate.", "sync-v2", datetime(2026, 9, 4, 2, tzinfo=UTC),
        "c" * 64, datetime(2026, 9, 4, tzinfo=UTC),
        datetime(2026, 9, 4, 2, tzinfo=UTC), "active", "published",
        {"snapshot": "sync-v2"}, consecutive_misses=0, official_status_code=1,
        source_snapshot_at=datetime(2026, 9, 4, 3, tzinfo=UTC),
    )
    newest = repository.project_official_version(base)
    older = repository.project_official_version(replace(
        base,
        official_position_version_id=uuid4(),
        client_request_id=uuid4(),
        source_version="sync-v1",
        source_changed_at=base.source_changed_at,
        last_observed_at=base.last_observed_at,
        official_status="stale",
        status_reason="older snapshot",
        source_snapshot_at=datetime(2026, 9, 4, 2, tzinfo=UTC),
    ))

    assert older.official_position_version_id != newest.official_position_version_id
    assert repository.official_version(
        owner_id, position_id, newest.official_position_version_id
    ) == newest
    with psycopg.connect(environment["admin"]) as connection:
        pointer = connection.execute(
            "select current_official_version_id from platform_hr.positions "
            "where position_id=%s", (position_id,),
        ).fetchone()[0]
    assert pointer == newest.official_position_version_id


@pytest.mark.postgres
def test_task_request_is_durable_and_replay_compares_full_payload(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Task Request Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "研发工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    command = CreatePositionTaskRequest(
        uuid4(), owner_id, position.position_id, uuid4(), "e" * 64,
        "jd", None,
    )

    first = repository.create_task_request(command)
    assert repository.create_task_request(command) == first
    assert repository.task_request(
        owner_id, position.position_id, command.client_request_id
    ) == first
    with pytest.raises(PositionContextConflict):
        repository.create_task_request(replace(command, task_kind="jr"))


@pytest.mark.postgres
def test_default_service_replays_task_request_and_context_draft_with_same_ids(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Service Replay Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "重试岗位"))
    service = PositionIntelligenceService(PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    ))
    task_request_id = uuid4()
    draft_request_id = uuid4()
    task_args = dict(
        owner_id=owner_id, position_id=position.position_id,
        request_id=task_request_id, canonical_payload_sha256="8" * 64,
        task_kind="jd", expected_context_version_id=None,
    )
    draft_args = dict(
        owner_id=owner_id, position_id=position.position_id,
        request_id=draft_request_id, base_context_version_id=None,
        official_version_id=None, modules={"mission": {"text": "stable"}},
        summary="stable draft",
    )

    assert service.create_task_request(**task_args) == service.create_task_request(
        **task_args
    )
    assert service.create_draft(**draft_args) == service.create_draft(**draft_args)


@pytest.mark.postgres
def test_context_draft_rejects_cross_position_conversation_and_artifact_provenance(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Context Provenance Owner")
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    target = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "目标上下文岗位")
    )
    other = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "其他上下文岗位")
    )
    conversation_id = uuid4()
    artifact_id, artifact_version_id, attachment_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.conversations("
            "conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,direct_agent_id,title) values (%s,%s,%s,'direct_agent','hr-bot','来源')",
            (conversation_id, owner_id, uuid4()),
        )
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,size_bytes,sha256,retained_until,"
            "state,ready_at) values (%s,%s,%s,'agent_output',%s,1,%s,1,'version:v1',1,"
            "%s,now()+interval '1 day','ready',now())",
            (attachment_id, owner_id, conversation_id, b"a" * 29, b"b" * 29, b"c" * 32),
        )
        connection.execute(
            "alter table platform_attachments.artifacts disable trigger "
            "enforce_artifact_task_context_v64"
        )
        connection.execute(
            "insert into platform_attachments.artifacts("
            "artifact_id,artifact_key,owner_internal_user_id,conversation_id,task_id,agent_id) "
            "values (%s,'context-source',%s,%s,%s,'hr-bot')",
            (artifact_id, owner_id, conversation_id, uuid4()),
        )
        connection.execute(
            "alter table platform_attachments.artifacts enable trigger "
            "enforce_artifact_task_context_v64"
        )
        connection.execute(
            "insert into platform_attachments.artifact_versions("
            "artifact_version_id,artifact_id,attachment_id,version_no,producer_version_id,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,size_bytes,sha256,retained_until,state,"
            "result_status) values (%s,%s,%s,1,'v1',%s,1,%s,1,'version:v1',1,%s,"
            "now()+interval '1 day','ready','succeeded')",
            (artifact_version_id, artifact_id, attachment_id,
             b"a" * 29, b"b" * 29, b"c" * 32),
        )
    positions.bind_conversation(BindPositionConversation(
        owner_id, other.position_id, conversation_id, uuid4(), "created_in_position",
    ))
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_hr.position_artifacts("
            "position_id,artifact_id,owner_internal_user_id,client_request_id) "
            "values (%s,%s,%s,%s)",
            (other.position_id, artifact_id, owner_id, uuid4()),
        )
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    base = CreateContextDraft(
        owner_id, uuid4(), target.position_id, None, None,
        {"mission": {"text": "isolated"}}, "isolated", uuid4(),
    )

    with pytest.raises(PositionContextNotFound):
        repository.create_draft(replace(
            base, source_conversation_id=conversation_id,
        ))
    with pytest.raises(PositionContextNotFound):
        repository.create_draft(replace(
            base, context_version_id=uuid4(), client_request_id=uuid4(),
            source_artifact_version_id=artifact_version_id,
        ))
