from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.hr.import_cli import execute_import, inspect_snapshot
from app.hr.importers import OfficialJobSnapshot
from test_hr_position_importers import _job, _snapshot


def test_import_cli_inspection_emits_safe_snapshot_summary_only() -> None:
    summary = inspect_snapshot(_snapshot(_job(requirement="PRIVATE INTERNAL TEXT")))

    assert summary == {
        "version": "20260904T010000Z-a1b2c3",
        "last_successful_sync_at": "2026-09-04T01:00:00+00:00",
        "job_count": 1,
        "statuses": {"active": 1},
    }
    assert "PRIVATE INTERNAL TEXT" not in json.dumps(summary)


class _Conversations:
    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id

    def list_for_owner(self, owner_id, *, limit, before=None, direct_agent_id, status):
        assert limit == 101
        assert direct_agent_id == "hr-bot"
        if before is not None or status == "archived":
            return ()
        return (SimpleNamespace(
            conversation_id=self.conversation_id,
            title="J11014 算法岗位",
            updated_at=datetime(2026, 9, 4, tzinfo=UTC),
        ),)

    def messages_after(self, owner_id, conversation_id, *, after, limit):
        assert conversation_id == self.conversation_id
        assert limit == 201
        if after:
            return ()
        return (
            SimpleNamespace(seq=1, role="user", content="分析 J11014"),
            SimpleNamespace(seq=2, role="assistant", content="private answer"),
        )


class _Positions:
    def __init__(self) -> None:
        self.projected = []
        self.bound = []
        self.proposed = []
        self.evidence = []

    def project_official(self, command, *, import_evidence=None):
        self.projected.append(command)
        self.evidence.append(import_evidence)
        return SimpleNamespace(position_id=command.position_id)

    def bind_conversation(self, command, *, import_evidence=None):
        self.bound.append(command)
        self.evidence.append(import_evidence)
        return command

    def propose_draft(self, command, *, import_evidence=None):
        self.proposed.append(command)
        self.evidence.append(import_evidence)
        return command


def test_import_dry_run_reads_owner_scoped_hr_history_without_mutating() -> None:
    owner_id, conversation_id = uuid4(), uuid4()
    positions = _Positions()

    summary = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=uuid4(),
        position_repository=positions,
        conversation_repository=_Conversations(conversation_id),
        rule_version="history-r11",
        apply=False,
    )

    assert summary == {
        "mode": "dry-run",
        "run_id": str(summary["run_id"]),
        "snapshot_version": "20260904T010000Z-a1b2c3",
        "official_positions": 1,
        "hr_conversations": 1,
        "exact_bindings": 1,
        "drafts": 0,
        "skipped_conversations": 0,
    }
    assert positions.projected == positions.bound == positions.proposed == []
    assert "private answer" not in json.dumps(summary)


def test_import_apply_projects_and_binds_with_the_supplied_stable_run_id() -> None:
    owner_id, conversation_id, request_id = uuid4(), uuid4(), uuid4()
    positions = _Positions()

    summary = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=request_id,
        position_repository=positions,
        conversation_repository=_Conversations(conversation_id),
        rule_version="history-r11",
        apply=True,
    )

    assert summary["mode"] == "apply"
    assert len(positions.projected) == len(positions.bound) == 1
    assert positions.proposed == []
    assert positions.projected[0].owner_id == owner_id
    assert positions.bound[0].conversation_id == conversation_id
