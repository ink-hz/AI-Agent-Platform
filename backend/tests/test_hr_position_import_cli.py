from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from test_hr_position_importers import _job, _snapshot

from app.hr.import_cli import (
    PsycopgHistoricalResourceRepository,
    execute_import,
    inspect_snapshot,
)
from app.hr.importers import OfficialJobSnapshot
from app.hr.resource_backfill import (
    HistoricalConversationResources,
    HistoricalPositionBinding,
    ResourceBinding,
)


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
        self.official_versions = []
        self.bound = []
        self.proposed = []
        self.materials = []
        self.artifacts = []
        self.evidence = []

    def project_official(self, command, *, import_evidence=None):
        self.projected.append(command)
        self.evidence.append(import_evidence)
        return SimpleNamespace(position_id=command.position_id)

    def project_official_version(self, command):
        self.official_versions.append(command)
        return command

    def bind_conversation(self, command, *, import_evidence=None):
        self.bound.append(command)
        self.evidence.append(import_evidence)
        return command

    def propose_draft(self, command, *, import_evidence=None):
        self.proposed.append(command)
        self.evidence.append(import_evidence)
        return command

    def promote_material(self, command):
        self.materials.append(command)
        return command

    def link_artifact(self, owner_id, position_id, artifact_id, request_id):
        self.artifacts.append((owner_id, position_id, artifact_id, request_id))


class _Resources:
    def __init__(self, resources=(), position_bindings=()) -> None:
        self.resources = tuple(resources)
        self.position_bindings = tuple(position_bindings)
        self.reads = []
        self.applied = []
        self._linked = set()

    def conversation_resources(self, owner_id, conversation_ids):
        self.reads.append(("resources", owner_id, conversation_ids))
        return self.resources

    def position_bindings_for_conversations(self, owner_id, conversation_ids):
        self.reads.append(("positions", owner_id, conversation_ids))
        return self.position_bindings

    def apply_resource_binding(self, binding):
        self.applied.append(binding)
        key = (
            binding.owner_id,
            binding.position_id,
            binding.resource_kind,
            binding.resource_id,
        )
        if key in self._linked:
            return False
        self._linked.add(key)
        return True


def test_import_dry_run_reads_owner_scoped_hr_history_without_mutating() -> None:
    owner_id, conversation_id, attachment_id, artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    positions = _Positions()
    resources = _Resources((HistoricalConversationResources(
        conversation_id, owner_id, (attachment_id,), (artifact_id,),
    ),))

    summary = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=uuid4(),
        position_repository=positions,
        conversation_repository=_Conversations(conversation_id),
        resource_repository=resources,
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
        "exact_materials": 1,
        "exact_artifacts": 1,
        "ambiguous_attachments": 0,
        "ambiguous_artifacts": 0,
        "applied": 0,
        "noop": 0,
    }
    assert positions.projected == positions.bound == positions.proposed == []
    assert resources.applied == []
    assert resources.reads == [
        ("resources", owner_id, (conversation_id,)),
        ("positions", owner_id, (conversation_id,)),
    ]
    assert "private answer" not in json.dumps(summary)


def test_import_apply_projects_and_binds_with_the_supplied_stable_run_id() -> None:
    owner_id, conversation_id, request_id, attachment_id, artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    positions = _Positions()
    resources = _Resources((HistoricalConversationResources(
        conversation_id, owner_id, (attachment_id,), (artifact_id,),
    ),))

    first = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=request_id,
        position_repository=positions,
        conversation_repository=_Conversations(conversation_id),
        resource_repository=resources,
        rule_version="history-r11",
        apply=True,
    )
    first_requests = tuple(binding.request_id for binding in resources.applied)
    replay = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=request_id,
        position_repository=positions,
        conversation_repository=_Conversations(conversation_id),
        resource_repository=resources,
        rule_version="history-r11",
        apply=True,
    )

    assert first["mode"] == "apply"
    assert first["applied"] == 2
    assert first["noop"] == 0
    assert replay["applied"] == 0
    assert replay["noop"] == 2
    assert first_requests == tuple(
        binding.request_id for binding in resources.applied[2:]
    )
    assert (
        len(positions.projected)
        == len(positions.official_versions)
        == len(positions.bound)
        == 2
    )
    assert positions.proposed == []
    assert positions.projected[0].owner_id == owner_id
    assert positions.bound[0].conversation_id == conversation_id
    assert positions.official_versions[0].duty == "Build the system."
    assert "Build the system." not in json.dumps(first)


def test_import_keeps_conflicting_existing_and_new_position_bindings_ambiguous() -> None:
    owner_id, conversation_id, attachment_id, artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    existing_position_id = uuid4()
    resources = _Resources(
        (HistoricalConversationResources(
            conversation_id, owner_id, (attachment_id,), (artifact_id,),
        ),),
        (HistoricalPositionBinding(
            conversation_id, owner_id, existing_position_id,
        ),),
    )

    summary = execute_import(
        snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
        owner_id=owner_id,
        request_id=uuid4(),
        position_repository=_Positions(),
        conversation_repository=_Conversations(conversation_id),
        resource_repository=resources,
        rule_version="history-r11",
        apply=True,
    )

    assert summary["exact_materials"] == summary["exact_artifacts"] == 0
    assert summary["ambiguous_attachments"] == 1
    assert summary["ambiguous_artifacts"] == 1
    assert summary["applied"] == summary["noop"] == 0
    assert resources.applied == []


def test_import_rejects_cross_owner_resource_projections() -> None:
    owner_id, other_owner, conversation_id = uuid4(), uuid4(), uuid4()
    resources = _Resources((HistoricalConversationResources(
        conversation_id, other_owner, (uuid4(),), (),
    ),))

    try:
        execute_import(
            snapshot=OfficialJobSnapshot.parse(_snapshot(_job())),
            owner_id=owner_id,
            request_id=uuid4(),
            position_repository=_Positions(),
            conversation_repository=_Conversations(conversation_id),
            resource_repository=resources,
            rule_version="history-r11",
            apply=True,
        )
    except ValueError as error:
        assert str(error) == "historical resource scope invalid"
    else:
        raise AssertionError("cross-owner historical resource accepted")

    assert resources.applied == []


class _Result:
    def __init__(self, rows=()) -> None:
        self.rows = tuple(rows)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, parameters=()):
        self.calls.append((" ".join(sql.split()), parameters))
        return self.results.pop(0)


def test_resource_adapter_reads_only_exact_owner_resources_with_available_bytes() -> None:
    owner_id, conversation_id = uuid4(), uuid4()
    attachment_id, missing_attachment_id, artifact_id, missing_artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    connection = _Connection((
        _Result((
            {
                "owner_internal_user_id": owner_id,
                "conversation_id": conversation_id,
                "attachment_id": attachment_id,
                "bytes_available": True,
            },
            {
                "owner_internal_user_id": owner_id,
                "conversation_id": conversation_id,
                "attachment_id": missing_attachment_id,
                "bytes_available": False,
            },
        )),
        _Result((
            {
                "owner_internal_user_id": owner_id,
                "conversation_id": conversation_id,
                "artifact_id": artifact_id,
                "bytes_available": True,
            },
            {
                "owner_internal_user_id": owner_id,
                "conversation_id": conversation_id,
                "artifact_id": missing_artifact_id,
                "bytes_available": False,
            },
        )),
    ))
    repository = PsycopgHistoricalResourceRepository(
        "postgresql://resources",
        _Positions(),
        connect=lambda *_args, **_kwargs: connection,
    )

    resources = repository.conversation_resources(owner_id, (conversation_id,))

    assert resources == (HistoricalConversationResources(
        conversation_id, owner_id, (attachment_id,), (artifact_id,),
    ),)
    attachment_sql, attachment_parameters = connection.calls[0]
    artifact_sql, artifact_parameters = connection.calls[1]
    assert "attachment.owner_internal_user_id=%s" in attachment_sql
    assert "attachment.conversation_id=any(%s::uuid[])" in attachment_sql
    assert "attachment.source_kind='user_input'" in attachment_sql
    assert "attachment.immutable_locator is not null" in attachment_sql
    assert "platform_attachments.erasure_jobs" in attachment_sql
    assert "platform_attachments.current_artifact_versions" in artifact_sql
    assert "version.result_status='succeeded'" in artifact_sql
    assert "version.immutable_locator is not null" in artifact_sql
    assert attachment_parameters == artifact_parameters == (
        owner_id, [conversation_id],
    )


def test_resource_adapter_reads_owner_scoped_position_conversation_bindings() -> None:
    owner_id, conversation_id, position_id = uuid4(), uuid4(), uuid4()
    connection = _Connection((_Result(({
        "owner_internal_user_id": owner_id,
        "conversation_id": conversation_id,
        "position_id": position_id,
    },)),))
    repository = PsycopgHistoricalResourceRepository(
        "postgresql://resources",
        _Positions(),
        connect=lambda *_args, **_kwargs: connection,
    )

    bindings = repository.position_bindings_for_conversations(
        owner_id, (conversation_id,)
    )

    assert bindings == (
        HistoricalPositionBinding(conversation_id, owner_id, position_id),
    )
    sql, parameters = connection.calls[0]
    assert "owner_internal_user_id=%s" in sql
    assert "conversation_id=any(%s::uuid[])" in sql
    assert parameters == (owner_id, [conversation_id])


def test_resource_adapter_uses_existing_linkers_and_reports_replay_noop() -> None:
    owner_id, position_id, attachment_id, artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    positions = _Positions()
    connections = iter((
        _Connection((_Result(),)),
        _Connection((_Result(),)),
        _Connection((_Result(({"present": True},)),)),
        _Connection((_Result(({"present": True},)),)),
    ))
    repository = PsycopgHistoricalResourceRepository(
        "postgresql://resources",
        positions,
        connect=lambda *_args, **_kwargs: next(connections),
    )
    material = ResourceBinding(
        owner_id, position_id, attachment_id, "material", uuid4()
    )
    artifact = ResourceBinding(
        owner_id, position_id, artifact_id, "artifact", uuid4()
    )

    assert repository.apply_resource_binding(material) is True
    assert repository.apply_resource_binding(artifact) is True
    assert repository.apply_resource_binding(material) is False
    assert repository.apply_resource_binding(artifact) is False
    assert positions.materials[0].client_request_id == material.request_id
    assert positions.artifacts == [(
        owner_id, position_id, artifact_id, artifact.request_id,
    )]
