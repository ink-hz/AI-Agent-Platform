from __future__ import annotations

import argparse
import json
import os
import stat
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import ConversationRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.local_secrets import read_secret_file

from .importers import (
    HistoricalConversation,
    HistoricalMessage,
    OfficialJobSnapshot,
    apply_historical_discovery,
    discover_historical_positions,
    project_official_jobs,
)
from .models import PromotePositionMaterial
from .position_intelligence_repository import PositionIntelligenceRepository
from .repository import HrPositionRepository
from .resource_backfill import (
    HistoricalConversationResources,
    HistoricalPositionBinding,
    HistoricalResourceRepository,
    ResourceBinding,
    apply_resource_bindings,
    discover_resource_bindings,
)


class _ImportRepository:
    def __init__(self, database_url: str) -> None:
        self._positions = HrPositionRepository(database_url)
        self._intelligence = PositionIntelligenceRepository(database_url)

    def project_official_version(self, command):
        return self._intelligence.project_official_version(command)

    def __getattr__(self, name: str):
        return getattr(self._positions, name)

DEFAULT_REGISTRY_FILE = (
    "/Users/agentops/AgentRuntime/instances/hr-bot/state/"
    "jd-sync/current/jobs.json"
)


class PsycopgHistoricalResourceRepository:
    """Read historical resource identities and reuse the existing HR linkers."""

    def __init__(
        self,
        database_url: str,
        position_repository,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("historical resource database URL required")
        if any(
            not callable(getattr(position_repository, name, None))
            for name in ("promote_material", "link_artifact")
        ) or not callable(connect):
            raise ValueError("historical resource repository invalid")
        self._database_url = database_url
        self._positions = position_repository
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    @staticmethod
    def _scope(
        owner_id: UUID, conversation_ids: tuple[UUID, ...]
    ) -> tuple[UUID, tuple[UUID, ...]]:
        if (
            not isinstance(owner_id, UUID)
            or not isinstance(conversation_ids, tuple)
            or any(not isinstance(value, UUID) for value in conversation_ids)
            or len(conversation_ids) != len(set(conversation_ids))
        ):
            raise ValueError("historical resource scope invalid")
        return owner_id, conversation_ids

    @staticmethod
    def _row_in_scope(
        row, owner_id: UUID, conversation_ids: set[UUID]
    ) -> bool:
        return (
            row.get("owner_internal_user_id") == owner_id
            and row.get("conversation_id") in conversation_ids
        )

    def conversation_resources(
        self, owner_id: UUID, conversation_ids: tuple[UUID, ...]
    ) -> tuple[HistoricalConversationResources, ...]:
        owner_id, conversation_ids = self._scope(owner_id, conversation_ids)
        if not conversation_ids:
            return ()
        parameters = (owner_id, list(conversation_ids))
        with self._connection() as connection:
            attachment_rows = connection.execute(
                "select attachment.owner_internal_user_id,"
                "attachment.conversation_id,attachment.attachment_id,"
                "(attachment.state='ready' and attachment.ready_at is not null "
                "and attachment.deleted_at is null "
                "and attachment.retained_until>now() "
                "and attachment.immutable_locator is not null and not exists ("
                "select 1 from platform_attachments.erasure_jobs erasure "
                "where erasure.attachment_id=attachment.attachment_id)) "
                "as bytes_available from platform_attachments.attachments attachment "
                "where attachment.owner_internal_user_id=%s "
                "and attachment.conversation_id=any(%s::uuid[]) "
                "and attachment.source_kind='user_input' "
                "order by attachment.conversation_id,attachment.attachment_id",
                parameters,
            ).fetchall()
            artifact_rows = connection.execute(
                "select artifact.owner_internal_user_id,artifact.conversation_id,"
                "artifact.artifact_id,exists(select 1 from "
                "platform_attachments.current_artifact_versions version join "
                "platform_attachments.attachments attachment "
                "on attachment.attachment_id=version.attachment_id "
                "where version.artifact_id=artifact.artifact_id "
                "and version.state='ready' and version.result_status='succeeded' "
                "and version.retained_until>now() "
                "and version.immutable_locator is not null "
                "and attachment.owner_internal_user_id=artifact.owner_internal_user_id "
                "and attachment.state='ready' and attachment.ready_at is not null "
                "and attachment.deleted_at is null "
                "and attachment.retained_until>now() "
                "and attachment.immutable_locator is not null and not exists ("
                "select 1 from platform_attachments.erasure_jobs erasure "
                "where erasure.attachment_id=attachment.attachment_id)) "
                "as bytes_available from platform_attachments.artifacts artifact "
                "where artifact.owner_internal_user_id=%s "
                "and artifact.conversation_id=any(%s::uuid[]) "
                "order by artifact.conversation_id,artifact.artifact_id",
                parameters,
            ).fetchall()
        selected_ids = set(conversation_ids)
        if any(
            not self._row_in_scope(row, owner_id, selected_ids)
            for row in (*attachment_rows, *artifact_rows)
        ):
            raise ValueError("historical resource scope invalid")
        attachments: dict[UUID, list[UUID]] = {
            conversation_id: [] for conversation_id in conversation_ids
        }
        artifacts: dict[UUID, list[UUID]] = {
            conversation_id: [] for conversation_id in conversation_ids
        }
        for row in attachment_rows:
            if row.get("bytes_available") is True:
                attachments[row["conversation_id"]].append(row["attachment_id"])
        for row in artifact_rows:
            if row.get("bytes_available") is True:
                artifacts[row["conversation_id"]].append(row["artifact_id"])
        return tuple(
            HistoricalConversationResources(
                conversation_id,
                owner_id,
                tuple(attachments[conversation_id]),
                tuple(artifacts[conversation_id]),
            )
            for conversation_id in conversation_ids
        )

    def position_bindings_for_conversations(
        self, owner_id: UUID, conversation_ids: tuple[UUID, ...]
    ) -> tuple[HistoricalPositionBinding, ...]:
        owner_id, conversation_ids = self._scope(owner_id, conversation_ids)
        if not conversation_ids:
            return ()
        with self._connection() as connection:
            rows = connection.execute(
                "select owner_internal_user_id,conversation_id,position_id "
                "from platform_hr.position_conversations "
                "where owner_internal_user_id=%s "
                "and conversation_id=any(%s::uuid[]) "
                "order by conversation_id,position_id",
                (owner_id, list(conversation_ids)),
            ).fetchall()
        selected_ids = set(conversation_ids)
        if any(
            not self._row_in_scope(row, owner_id, selected_ids) for row in rows
        ):
            raise ValueError("historical resource scope invalid")
        return tuple(
            HistoricalPositionBinding(
                row["conversation_id"], owner_id, row["position_id"]
            )
            for row in rows
        )

    def apply_resource_binding(self, binding: ResourceBinding) -> bool:
        if not isinstance(binding, ResourceBinding):
            raise ValueError("historical resource binding required")
        if binding.resource_kind == "material":
            query = (
                "select 1 as present from platform_hr.position_materials "
                "where owner_internal_user_id=%s and position_id=%s "
                "and attachment_id=%s and active"
            )
        else:
            query = (
                "select 1 as present from platform_hr.position_artifacts "
                "where owner_internal_user_id=%s and position_id=%s "
                "and artifact_id=%s"
            )
        with self._connection() as connection:
            existing = connection.execute(
                query,
                (binding.owner_id, binding.position_id, binding.resource_id),
            ).fetchone()
        if existing is not None:
            return False
        if binding.resource_kind == "material":
            self._positions.promote_material(PromotePositionMaterial(
                binding.owner_id,
                binding.position_id,
                binding.resource_id,
                binding.request_id,
            ))
        else:
            self._positions.link_artifact(
                binding.owner_id,
                binding.position_id,
                binding.resource_id,
                binding.request_id,
            )
        return True


def inspect_snapshot(payload: bytes) -> dict[str, object]:
    snapshot = OfficialJobSnapshot.parse(payload)
    return {
        "version": snapshot.version,
        "last_successful_sync_at": snapshot.last_successful_sync_at.isoformat(),
        "job_count": len(snapshot.jobs),
        "statuses": dict(sorted(Counter(job.status for job in snapshot.jobs).items())),
    }


def _owner_conversations(repository, owner_id: UUID) -> list[HistoricalConversation]:
    selected: list[HistoricalConversation] = []
    for status_value in ("active", "archived"):
        before: tuple[datetime, UUID] | None = None
        while True:
            page = repository.list_for_owner(
                owner_id,
                limit=101,
                before=before,
                direct_agent_id="hr-bot",
                status=status_value,
            )
            batch = page[:100]
            for conversation in batch:
                messages: list[HistoricalMessage] = []
                after = 0
                while True:
                    message_page = repository.messages_after(
                        owner_id,
                        conversation.conversation_id,
                        after=after,
                        limit=201,
                    )
                    message_batch = message_page[:200]
                    messages.extend(
                        HistoricalMessage(message.seq, message.content)
                        for message in message_batch
                        if message.role == "user" and message.content.strip()
                    )
                    if len(message_page) <= 200:
                        break
                    after = message_batch[-1].seq
                selected.append(HistoricalConversation(
                    conversation.conversation_id,
                    conversation.title,
                    tuple(messages),
                ))
            if len(page) <= 100:
                break
            last = batch[-1]
            before = (last.updated_at, last.conversation_id)
    return selected


def execute_import(
    *,
    snapshot: OfficialJobSnapshot,
    owner_id: UUID,
    request_id: UUID,
    position_repository,
    conversation_repository,
    resource_repository: HistoricalResourceRepository,
    rule_version: str,
    apply: bool,
) -> dict[str, object]:
    if not isinstance(snapshot, OfficialJobSnapshot):
        raise ValueError("official snapshot required")
    if not isinstance(owner_id, UUID) or not isinstance(request_id, UUID):
        raise ValueError("import identifiers invalid")
    conversations = _owner_conversations(conversation_repository, owner_id)
    official_titles = {job.canonical_id: job.title for job in snapshot.jobs}
    discovery = discover_historical_positions(
        conversations, official_titles, rule_version=rule_version,
    )
    conversation_ids = tuple(
        conversation.conversation_id for conversation in conversations
    )
    historical_resources = resource_repository.conversation_resources(
        owner_id, conversation_ids
    )
    persisted_bindings = resource_repository.position_bindings_for_conversations(
        owner_id, conversation_ids
    )
    conversation_id_set = set(conversation_ids)
    if any(
        resource.owner_id != owner_id
        or resource.conversation_id not in conversation_id_set
        for resource in historical_resources
    ) or any(
        binding.owner_id != owner_id
        or binding.conversation_id not in conversation_id_set
        for binding in persisted_bindings
    ):
        raise ValueError("historical resource scope invalid")
    planned_bindings = tuple(
        HistoricalPositionBinding(
            link.conversation_id,
            owner_id,
            uuid5(owner_id, f"official-position:{link.official_job_id}"),
        )
        for link in discovery.exact_links
    )
    resource_discovery = discover_resource_bindings(
        historical_resources, (*persisted_bindings, *planned_bindings)
    )
    resource_application = None
    if apply:
        projected = project_official_jobs(
            snapshot, position_repository, owner_id, request_id,
        )
        official_ids = {
            job.canonical_id: record.position_id
            for job, record in zip(snapshot.jobs, projected, strict=True)
        }
        apply_historical_discovery(
            discovery, official_ids, position_repository, owner_id, request_id,
        )
        resource_application = apply_resource_bindings(
            resource_discovery, resource_repository.apply_resource_binding
        )
    resource_counts = resource_discovery.counts()
    return {
        "mode": "apply" if apply else "dry-run",
        "run_id": str(request_id),
        "snapshot_version": snapshot.version,
        "official_positions": len(snapshot.jobs),
        "hr_conversations": len(conversations),
        "exact_bindings": len(discovery.exact_links),
        "drafts": len(discovery.drafts),
        "skipped_conversations": len(discovery.skipped_conversation_ids),
        **resource_counts,
        "applied": (
            resource_application.applied_count
            if resource_application is not None
            else 0
        ),
        "noop": (
            resource_application.noop_count
            if resource_application is not None
            else 0
        ),
    }


def _read_snapshot(path_value: str) -> OfficialJobSnapshot:
    path = Path(path_value)
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("official registry must be a regular file")
    if metadata.st_size <= 0 or metadata.st_size > 32_000_000:
        raise ValueError("official registry size invalid")
    return OfficialJobSnapshot.parse(path.read_bytes())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hr-position-import",
        description="Project the published HR JD registry and existing HR conversations",
    )
    parser.add_argument(
        "--registry-file",
        default=os.getenv("METABOT_HR_JD_REGISTRY_FILE", DEFAULT_REGISTRY_FILE),
    )
    parser.add_argument(
        "--database-url-file",
        default=os.getenv("PLATFORM_CONTROL_DATABASE_URL_FILE"),
        required=not bool(os.getenv("PLATFORM_CONTROL_DATABASE_URL_FILE")),
    )
    parser.add_argument(
        "--content-keyring-file",
        default=os.getenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"),
        required=not bool(os.getenv("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE")),
    )
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--rule-version", default="historical-r11")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    snapshot = _read_snapshot(namespace.registry_file)
    request_id = namespace.run_id or uuid5(
        namespace.owner_id,
        f"hr-position-import:{snapshot.version}:{namespace.rule_version}",
    )
    database_url = read_secret_file(namespace.database_url_file)
    keyring = IdentityKeyring.from_file(
        namespace.content_keyring_file,
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    codec = ContentCodec(keyring)
    position_repository = _ImportRepository(database_url)
    summary = execute_import(
        snapshot=snapshot,
        owner_id=namespace.owner_id,
        request_id=request_id,
        position_repository=position_repository,
        conversation_repository=ConversationRepository(
            database_url, content_codec=codec,
        ),
        resource_repository=PsycopgHistoricalResourceRepository(
            database_url, position_repository
        ),
        rule_version=namespace.rule_version,
        apply=namespace.apply,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
