from __future__ import annotations

import json
from uuid import uuid4

import pytest
from app.hr.importers import (
    HistoricalConversation,
    HistoricalMessage,
    OfficialJobSnapshot,
    apply_historical_discovery,
    discover_historical_positions,
    project_official_jobs,
)


def _job(job_id: str = "J11014", **overrides):
    value = {
        "canonicalId": job_id,
        "jobAdId": 11014,
        "sourceRecordIds": ["source-J11014"],
        "title": "算法工程师",
        "category": "研发",
        "subcategory": "算法类",
        "locations": ["广东省·深圳市"],
        "organization": "机器人",
        "headcount": 1,
        "degree": "本科",
        "employmentType": "全职",
        "salary": "20K-30K 元/月",
        "duty": "Build the system.",
        "requirement": "Test the system.",
        "sourceChangedAt": "2026-09-04T01:00:00.000Z",
        "firstSeenAt": "2026-09-01T01:00:00.000Z",
        "lastSeenAt": "2026-09-04T01:00:00.000Z",
        "status": "active",
        "statusReason": "present_in_official_snapshot",
        "consecutiveMisses": 0,
        "contentHash": "a" * 64,
        "officialStatus": 1,
    }
    value.update(overrides)
    return value


def _snapshot(*jobs) -> bytes:
    return json.dumps({
        "version": "20260904T010000Z-a1b2c3",
        "lastSuccessfulSyncAt": "2026-09-04T01:00:00.000Z",
        "jobs": list(jobs) or [_job()],
    }).encode()


def test_official_snapshot_accepts_only_complete_published_registry_contract() -> None:
    snapshot = OfficialJobSnapshot.parse(_snapshot())

    assert snapshot.version == "20260904T010000Z-a1b2c3"
    assert snapshot.jobs[0].canonical_id == "J11014"
    assert snapshot.jobs[0].locations == ("广东省·深圳市",)

    document = json.loads(_snapshot())
    document["jobs"][0]["candidateName"] = "forbidden"
    with pytest.raises(ValueError, match="registry job fields invalid"):
        OfficialJobSnapshot.parse(json.dumps(document).encode())


def test_official_snapshot_accepts_sync_fallback_jobad_identifier() -> None:
    snapshot = OfficialJobSnapshot.parse(_snapshot(_job(
        "jobad:511189333", jobAdId=511189333,
    )))

    assert snapshot.jobs[0].canonical_id == "JOBAD:511189333"


@pytest.mark.parametrize("job_id", ["J1", "11014", "J-11014", "J1234567890123"])
def test_official_snapshot_rejects_noncanonical_or_duplicate_ids(job_id: str) -> None:
    with pytest.raises(ValueError, match="official job id invalid"):
        OfficialJobSnapshot.parse(_snapshot(_job(job_id)))
    with pytest.raises(ValueError, match="duplicate official job id"):
        OfficialJobSnapshot.parse(_snapshot(_job(), _job()))


class RecordingOfficialRepository:
    def __init__(self) -> None:
        self.commands = []
        self.versions = []
        self.evidence = []

    def project_official(self, command, *, import_evidence=None):
        self.commands.append(command)
        self.evidence.append(import_evidence)
        return command

    def project_official_version(self, command):
        self.versions.append(command)
        return command


def test_official_projection_is_deterministic_and_preserves_registry_status() -> None:
    owner_id, request_id = uuid4(), uuid4()
    repository = RecordingOfficialRepository()
    first = OfficialJobSnapshot.parse(_snapshot())
    changed = OfficialJobSnapshot.parse(_snapshot(_job(
        title="高级算法工程师", status="suspected_inactive",
        statusReason="missing_from_latest_snapshot", consecutiveMisses=1,
        contentHash="b" * 64,
    )))

    project_official_jobs(first, repository, owner_id, request_id)
    project_official_jobs(first, repository, owner_id, request_id)
    project_official_jobs(changed, repository, owner_id, request_id)

    assert repository.commands[0].position_id == repository.commands[1].position_id
    assert repository.commands[0].client_request_id == repository.commands[1].client_request_id
    assert repository.commands[2].position_id == repository.commands[0].position_id
    assert repository.commands[2].title == "高级算法工程师"
    assert repository.commands[2].official_status == "suspected_inactive"
    assert repository.commands[2].source_version == changed.version
    assert all(repository.evidence)


def test_official_import_preserves_duty_requirement_and_hash() -> None:
    owner_id, request_id = uuid4(), uuid4()
    repository = RecordingOfficialRepository()
    snapshot = OfficialJobSnapshot.parse(_snapshot())

    projected = project_official_jobs(snapshot, repository, owner_id, request_id)

    assert projected[0].official_version.duty == "Build the system."
    assert projected[0].official_version.requirement == "Test the system."
    assert projected[0].official_version.content_hash == "a" * 64
    assert projected[0].official_version.position_id == projected[0].position.position_id
    assert projected[0].official_version.consecutive_misses == 0
    assert projected[0].official_version.official_status_code == 1


def test_historical_discovery_links_only_one_known_complete_job_id() -> None:
    conversation_id = uuid4()
    discovered = discover_historical_positions(
        [HistoricalConversation(
            conversation_id,
            "算法岗位分析",
            (HistoricalMessage(2, "请分析 J11014 的人才画像"),),
        )],
        {"J11014": "算法工程师"},
        rule_version="history-v1",
    )

    assert [(item.conversation_id, item.official_job_id) for item in discovered.exact_links] == [
        (conversation_id, "J11014")
    ]
    assert discovered.drafts == ()


def test_historical_title_evidence_is_not_misattributed_to_message_one() -> None:
    owner_id, request_id, conversation_id, position_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    discovery = discover_historical_positions(
        [HistoricalConversation(conversation_id, "J11014 算法岗位分析", ())],
        {"J11014": "算法工程师"},
        rule_version="history-v1",
    )
    repository = RecordingHistoricalRepository()

    apply_historical_discovery(
        discovery, {"J11014": position_id}, repository, owner_id, request_id
    )

    assert discovery.exact_links[0].message_sequence is None
    assert repository.evidence[0]["source_message_seq"] is None
    assert repository.evidence[0]["evidence"]["source_location"] == "title"


def test_historical_title_only_draft_records_title_provenance() -> None:
    conversation_id = uuid4()
    discovery = discover_historical_positions(
        [HistoricalConversation(conversation_id, "高级结构工程师招聘", ())],
        {},
        rule_version="history-v1",
    )

    assert discovery.drafts[0].evidence == {
        "job_ids": [],
        "message_seq": None,
        "source_location": "title",
    }


def test_historical_discovery_keeps_ambiguous_and_multi_position_work_unbound() -> None:
    ambiguous_id, multi_id, unrelated_id = uuid4(), uuid4(), uuid4()
    discovered = discover_historical_positions(
        [
            HistoricalConversation(
                ambiguous_id, "高级结构工程师招聘", (HistoricalMessage(1, "做岗位画像"),)
            ),
            HistoricalConversation(
                multi_id, "研发岗位对比", (
                    HistoricalMessage(3, "比较 J11014 和 J11015 的要求"),
                )
            ),
            HistoricalConversation(
                unrelated_id, "周报润色", (HistoricalMessage(1, "优化这段文字"),)
            ),
        ],
        {"J11014": "算法工程师", "J11015": "光学设计工程师"},
        rule_version="history-v1",
    )

    assert discovered.exact_links == ()
    assert [(item.conversation_id, item.title) for item in discovered.drafts] == [
        (ambiguous_id, "高级结构工程师招聘"),
        (multi_id, "算法工程师"),
        (multi_id, "光学设计工程师"),
    ]
    assert all(item.rule_version == "history-v1" for item in discovered.drafts)
    assert discovered.skipped_conversation_ids == (unrelated_id,)


class RecordingHistoricalRepository:
    def __init__(self) -> None:
        self.bindings = []
        self.drafts = []
        self.evidence = []

    def bind_conversation(self, command, *, import_evidence=None):
        self.bindings.append(command)
        self.evidence.append(import_evidence)
        return command

    def propose_draft(self, command, *, import_evidence=None):
        self.drafts.append(command)
        self.evidence.append(import_evidence)
        return command


def test_historical_application_is_replay_stable_and_never_replays_agent_turns() -> None:
    owner_id, request_id, exact_id, ambiguous_id, position_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    discovery = discover_historical_positions(
        [
            HistoricalConversation(exact_id, "算法岗位", (
                HistoricalMessage(2, "分析 J11014"),
            )),
            HistoricalConversation(ambiguous_id, "结构工程师招聘", (
                HistoricalMessage(1, "整理能力模型"),
            )),
        ],
        {"J11014": "算法工程师"},
        rule_version="history-v1",
    )
    repository = RecordingHistoricalRepository()

    apply_historical_discovery(
        discovery, {"J11014": position_id}, repository, owner_id, request_id
    )
    first_binding = repository.bindings[0]
    first_draft = repository.drafts[0]
    apply_historical_discovery(
        discovery, {"J11014": position_id}, repository, owner_id, request_id
    )

    assert repository.bindings[1] == first_binding
    assert first_binding.binding_kind == "historical_exact"
    assert repository.drafts[1] == first_draft
    assert first_draft.source_conversation_id == ambiguous_id
    assert not hasattr(repository, "run_turn")
