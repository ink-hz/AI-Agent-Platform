from __future__ import annotations

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from app.hr.models import (
    BindPositionConversation,
    ConfirmPositionDraft,
    CorrectPositionConversationBinding,
    CreateManualPosition,
    DismissPositionDraft,
    MergePositionDraft,
    PromotePositionMaterial,
    ProjectOfficialPosition,
    ProposePositionDraft,
)
from app.hr.repository import HrConflict, HrNotFound, HrPositionRepository, HrUnavailable
from test_control_plane_migration import control_database


def _owner(admin: psycopg.Connection, name: str):
    owner_id = uuid4()
    admin.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,%s,'active')",
        (owner_id, name),
    )
    admin.commit()
    return owner_id


def _hr_conversation(admin: psycopg.Connection, owner_id):
    conversation_id = uuid4()
    admin.execute(
        "insert into platform_control.conversations "
        "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status) values "
        "(%s,%s,%s,'direct_agent','hr-bot','岗位草拟','active')",
        (conversation_id, owner_id, uuid4()),
    )
    admin.commit()
    return conversation_id


@pytest.mark.postgres
def test_repository_creates_manual_position_idempotently_and_lists_for_owner(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "HR Position Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    command = CreateManualPosition(
        owner_id, uuid4(), uuid4(), " 3D 打印机高级结构工程师 ",
        "研发中心", ("深圳",),
    )

    first = repository.create_manual(command)
    replay = repository.create_manual(command)
    page = repository.list_positions(owner_id, limit=20)

    assert replay.position_id == first.position_id
    assert first.title == "3D 打印机高级结构工程师"
    assert page.items == (first,)
    assert page.next_cursor is None


@pytest.mark.postgres
def test_repository_serializes_concurrent_manual_position_replay(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Concurrent HR Position Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    command = CreateManualPosition(
        owner_id, uuid4(), uuid4(), "并发结构工程师", "研发", ("深圳",),
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _index: repository.create_manual(command), range(8)))

    assert {result.position_id for result in results} == {command.position_id}


@pytest.mark.postgres
def test_repository_rejects_reused_request_id_with_a_different_payload(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Conflicting HR Position Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    request_id = uuid4()
    repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), request_id, "结构工程师")
    )

    with pytest.raises(HrConflict):
        repository.create_manual(
            CreateManualPosition(owner_id, uuid4(), request_id, "算法工程师")
        )


@pytest.mark.postgres
def test_repository_projects_official_position_updates_without_duplication(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Official Projection Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    position_id = uuid4()
    first = repository.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "算法工程师", "机器人",
        ("深圳",), "active", "sync-v1", "a" * 64,
        datetime(2026, 9, 4, 1, tzinfo=UTC),
    ))
    changed = repository.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "高级算法工程师", "机器人",
        ("深圳", "中山"), "suspected_inactive", "sync-v2", "b" * 64,
        datetime(2026, 9, 4, 2, tzinfo=UTC),
    ))

    assert first.position_id == changed.position_id == position_id
    assert changed.title == "高级算法工程师"
    assert changed.official_status == "suspected_inactive"
    assert changed.source_version == "sync-v2"
    assert repository.list_positions(owner_id).items == (changed,)


@pytest.mark.postgres
def test_repository_never_rolls_an_official_position_back_to_an_older_snapshot(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Ordered Official Projection Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    position_id = uuid4()
    newer = repository.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "高级算法工程师", "机器人",
        ("深圳",), "active", "sync-v2", "b" * 64,
        datetime(2026, 9, 4, 2, tzinfo=UTC),
    ))
    older = repository.project_official(ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "算法工程师", "机器人",
        ("中山",), "inactive", "sync-v1", "a" * 64,
        datetime(2026, 9, 4, 1, tzinfo=UTC),
    ))

    assert older == newer
    assert older.title == "高级算法工程师"
    assert older.source_version == "sync-v2"


@pytest.mark.postgres
def test_official_projection_rolls_back_when_import_evidence_cannot_be_recorded(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Atomic Official Import Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    position_id = uuid4()
    command = ProjectOfficialPosition(
        owner_id, position_id, uuid4(), "J11014", "算法工程师", "机器人",
        ("深圳",), "active", "sync-v1", "a" * 64,
        datetime(2026, 9, 4, 1, tzinfo=UTC),
    )
    invalid_evidence = {
        "evidence_id": uuid4(),
        "owner_id": owner_id,
        "position_id": uuid4(),
        "draft_id": None,
        "source_conversation_id": None,
        "source_message_seq": None,
        "source_kind": "official_snapshot",
        "source_key": "J11014:a",
        "rule_version": "official-registry-v1",
        "evidence": {"content_hash": "a" * 64},
    }

    with pytest.raises(HrUnavailable):
        repository.project_official(command, import_evidence=invalid_evidence)

    assert repository.list_positions(owner_id).items == ()


@pytest.mark.postgres
def test_repository_rejects_agent_output_as_position_material(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Position Material Owner")
        attachment_id = uuid4()
        admin.execute(
            "insert into platform_attachments.attachments ("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,retained_until,"
            "state,ready_at) values (%s,%s,'agent_output',%s,1,%s,1,"
            "now()+interval '1 day','ready',now())",
            (attachment_id, owner_id, b"x" * 29, b"y" * 29),
        )
        admin.commit()
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "算法工程师")
    )

    with pytest.raises(HrNotFound):
        repository.promote_material(PromotePositionMaterial(
            owner_id, position.position_id, attachment_id, uuid4(),
        ))


@pytest.mark.postgres
def test_repository_conceals_positions_from_other_owners(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "First HR Owner")
        other_id = _owner(admin, "Other HR Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    created = repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "光学设计工程师")
    )

    with pytest.raises(HrNotFound):
        repository.position_for_owner(other_id, created.position_id)
    assert repository.list_positions(other_id, limit=20).items == ()


@pytest.mark.postgres
def test_repository_position_cursor_is_stable_and_exclusive(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Paged HR Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    for title in ("岗位一", "岗位二", "岗位三"):
        repository.create_manual(
            CreateManualPosition(owner_id, uuid4(), uuid4(), title)
        )

    first = repository.list_positions(owner_id, limit=2)
    second = repository.list_positions(owner_id, cursor=first.next_cursor, limit=2)

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert {item.position_id for item in first.items}.isdisjoint(
        item.position_id for item in second.items
    )


@pytest.mark.postgres
def test_repository_confirms_draft_and_binds_origin_conversation_atomically(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Draft HR Owner")
        conversation_id = _hr_conversation(admin, owner_id)
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    draft = repository.propose_draft(ProposePositionDraft(
        owner_id=owner_id, draft_id=uuid4(), client_request_id=uuid4(),
        source_kind="new_conversation", source_key=f"conversation:{conversation_id}",
        source_conversation_id=conversation_id, title="机器人算法工程师",
        proposal={"mission": "机器人感知"}, evidence={"message_seq": 1},
        discovery_rule_version="interactive-v1",
    ))
    command = ConfirmPositionDraft(
        owner_id, draft.draft_id, uuid4(), uuid4(), draft.row_version
    )

    position = repository.confirm_draft(command)
    replay = repository.confirm_draft(command)

    assert replay.position_id == position.position_id
    with psycopg.connect(environment["admin"]) as admin:
        binding = admin.execute(
            "select position_id,binding_kind from platform_hr.position_conversations "
            "where conversation_id=%s",
            (conversation_id,),
        ).fetchone()
    assert binding == (position.position_id, "draft_confirmed")
    detail = repository.position_for_owner(owner_id, position.position_id)
    assert detail.conversation_ids == (conversation_id,)


@pytest.mark.postgres
def test_repository_merges_and_dismisses_drafts_without_losing_evidence(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Draft Resolution Owner")
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    target = repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "高级结构工程师")
    )
    merged = repository.propose_draft(ProposePositionDraft(
        owner_id, uuid4(), uuid4(), "historical_conversation", "history:merge",
        None, "结构工程师", {"title": "结构工程师"},
        {"message_seq": 3, "excerpt": "结构岗位"}, "history-v1",
    ))
    dismissed = repository.propose_draft(ProposePositionDraft(
        owner_id, uuid4(), uuid4(), "historical_conversation", "history:dismiss",
        None, "无法确认的岗位", {}, {"message_seq": 7}, "history-v1",
    ))

    merged_result = repository.merge_draft(MergePositionDraft(
        owner_id, merged.draft_id, target.position_id, uuid4(), merged.row_version,
    ))
    dismissed_result = repository.dismiss_draft(DismissPositionDraft(
        owner_id, dismissed.draft_id, uuid4(), dismissed.row_version,
    ))

    assert merged_result.state == "merged"
    assert merged_result.resolved_position_id == target.position_id
    assert merged_result.evidence == {"excerpt": "结构岗位", "message_seq": 3}
    assert dismissed_result.state == "dismissed"
    assert dismissed_result.evidence == {"message_seq": 7}


@pytest.mark.postgres
def test_repository_corrects_binding_only_from_expected_position_and_audits(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Binding Correction Owner")
        conversation_id = _hr_conversation(admin, owner_id)
    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    previous = repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "岗位甲")
    )
    corrected = repository.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "岗位乙")
    )
    repository.bind_conversation(BindPositionConversation(
        owner_id, previous.position_id, conversation_id, uuid4(), "historical_exact"
    ))
    command = CorrectPositionConversationBinding(
        owner_id, conversation_id, previous.position_id, corrected.position_id,
        uuid4(), "历史岗位识别有误",
    )

    binding = repository.correct_conversation_binding(command)
    replay = repository.correct_conversation_binding(command)

    assert binding.position_id == corrected.position_id
    assert replay == binding
    assert binding.binding_kind == "manual_correction"
    with psycopg.connect(environment["admin"]) as admin:
        event = admin.execute(
            "select previous_position_id,new_position_id,reason from "
            "platform_hr.position_binding_events where conversation_id=%s",
            (conversation_id,),
        ).fetchone()
    assert event == (
        previous.position_id, corrected.position_id, "历史岗位识别有误"
    )
