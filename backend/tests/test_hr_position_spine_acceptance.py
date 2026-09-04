from __future__ import annotations

from dataclasses import fields
import json
from uuid import uuid4

import psycopg
import pytest

from app.hr.importers import (
    HistoricalConversation,
    HistoricalMessage,
    OfficialJobSnapshot,
    apply_historical_discovery,
    discover_historical_positions,
    project_official_jobs,
)
from app.hr.models import ConfirmPositionDraft, PositionDraftRecord, PositionRecord
from app.hr.repository import HrNotFound, HrPositionRepository
from test_control_plane_migration import control_database


def _owner(connection: psycopg.Connection, label: str):
    owner_id = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,%s,'active')",
        (owner_id, label),
    )
    return owner_id


def _conversation(connection: psycopg.Connection, owner_id, title: str):
    conversation_id = uuid4()
    connection.execute(
        "insert into platform_control.conversations "
        "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status) values "
        "(%s,%s,%s,'direct_agent','hr-bot',%s,'active')",
        (conversation_id, owner_id, uuid4(), title),
    )
    return conversation_id


def _official_snapshot() -> OfficialJobSnapshot:
    payload = {
        "version": "20260904T010000Z-r11",
        "lastSuccessfulSyncAt": "2026-09-04T01:00:00.000Z",
        "jobs": [{
            "canonicalId": "J11014", "jobAdId": 11014,
            "sourceRecordIds": ["source-J11014"], "title": "算法工程师",
            "category": "研发", "subcategory": "算法类", "locations": ["深圳"],
            "organization": "机器人", "headcount": 1, "degree": "本科",
            "employmentType": "全职", "salary": "面议", "duty": "构建感知算法",
            "requirement": "具备算法工程经验", "sourceChangedAt": "2026-09-04T01:00:00.000Z",
            "firstSeenAt": "2026-09-01T01:00:00.000Z", "lastSeenAt": "2026-09-04T01:00:00.000Z",
            "status": "active", "statusReason": "present_in_official_snapshot",
            "consecutiveMisses": 0, "contentHash": "a" * 64, "officialStatus": 1,
        }],
    }
    return OfficialJobSnapshot.parse(json.dumps(payload).encode())


@pytest.mark.postgres
def test_r11_import_review_binding_and_owner_boundary_survive_replay(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "R1.1 Acceptance Owner")
        other_owner_id = _owner(admin, "R1.1 Other Owner")
        exact_id = _conversation(admin, owner_id, "J11014 人才画像")
        ambiguous_id = _conversation(admin, owner_id, "高级结构工程师招聘")
        multi_id = _conversation(admin, owner_id, "J11014 与 J11015 岗位对比")
        admin.commit()
        turns_before = admin.execute(
            "select count(*) from platform_control.conversation_turns "
            "where conversation_id=any(%s)", ([exact_id, ambiguous_id, multi_id],),
        ).fetchone()[0]

    repository = HrPositionRepository(environment["urls"]["platform_control_app"])
    snapshot = _official_snapshot()
    import_request_id = uuid4()
    first = project_official_jobs(snapshot, repository, owner_id, import_request_id)
    replay = project_official_jobs(snapshot, repository, owner_id, import_request_id)

    assert len(first) == len(replay) == 1
    assert first[0].position_id == replay[0].position_id
    assert repository.list_positions(owner_id).items == first

    discovery = discover_historical_positions([
        HistoricalConversation(exact_id, "算法岗位", (HistoricalMessage(2, "分析 J11014"),)),
        HistoricalConversation(ambiguous_id, "高级结构工程师招聘", (HistoricalMessage(1, "梳理岗位画像"),)),
        HistoricalConversation(multi_id, "研发岗位对比", (HistoricalMessage(3, "比较 J11014 和 J11015"),)),
    ], {"J11014": "算法工程师"}, rule_version="history-r11")
    history_request_id = uuid4()
    first_apply = apply_historical_discovery(
        discovery, {"J11014": first[0].position_id}, repository, owner_id, history_request_id,
    )
    replay_apply = apply_historical_discovery(
        discovery, {"J11014": first[0].position_id}, repository, owner_id, history_request_id,
    )

    assert first_apply == replay_apply
    assert repository.position_for_conversation(owner_id, exact_id) == first[0].position_id
    assert repository.position_for_conversation(owner_id, multi_id) is None
    drafts = repository.list_drafts(owner_id, state="proposed")
    assert len(drafts) == 3
    assert len([draft for draft in drafts if draft.source_conversation_id == multi_id]) == 2
    with psycopg.connect(environment["admin"]) as admin:
        evidence = admin.execute(
            "select source_kind,source_conversation_id,source_message_seq,rule_version "
            "from platform_hr.position_import_evidence "
            "where owner_internal_user_id=%s order by source_kind,source_key",
            (owner_id,),
        ).fetchall()
    assert len(evidence) == 5
    assert (
        "historical_exact", exact_id, 2, "history-r11"
    ) in evidence

    ambiguous = next(draft for draft in drafts if draft.source_conversation_id == ambiguous_id)
    confirmed = repository.confirm_draft(ConfirmPositionDraft(
        owner_id, ambiguous.draft_id, uuid4(), uuid4(), ambiguous.row_version,
    ))
    assert repository.position_for_conversation(owner_id, ambiguous_id) == confirmed.position_id
    assert repository.position_for_owner(owner_id, confirmed.position_id).conversation_ids == (ambiguous_id,)
    with pytest.raises(HrNotFound):
        repository.position_for_owner(other_owner_id, confirmed.position_id)

    with psycopg.connect(environment["admin"]) as admin:
        turns_after = admin.execute(
            "select count(*) from platform_control.conversation_turns "
            "where conversation_id=any(%s)", ([exact_id, ambiguous_id, multi_id],),
        ).fetchone()[0]
    assert turns_after == turns_before == 0


def test_r11_domain_stays_recruiting_intelligence_instead_of_becoming_an_ats() -> None:
    forbidden = {
        "candidate", "recruiting_stage", "interview_schedule", "offer",
        "onboarding", "auto_contact", "auto_reject", "auto_hire", "beisen",
        "boss", "liepin", "oa",
    }
    model_fields = {
        field.name.lower()
        for model in (PositionRecord, PositionDraftRecord)
        for field in fields(model)
    }
    assert model_fields.isdisjoint(forbidden)
