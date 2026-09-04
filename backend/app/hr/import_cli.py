from __future__ import annotations

import argparse
import json
import os
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

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
from .position_intelligence_repository import PositionIntelligenceRepository
from .repository import HrPositionRepository


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
    return {
        "mode": "apply" if apply else "dry-run",
        "run_id": str(request_id),
        "snapshot_version": snapshot.version,
        "official_positions": len(snapshot.jobs),
        "hr_conversations": len(conversations),
        "exact_bindings": len(discovery.exact_links),
        "drafts": len(discovery.drafts),
        "skipped_conversations": len(discovery.skipped_conversation_ids),
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
    summary = execute_import(
        snapshot=snapshot,
        owner_id=namespace.owner_id,
        request_id=request_id,
        position_repository=_ImportRepository(database_url),
        conversation_repository=ConversationRepository(
            database_url, content_codec=codec,
        ),
        rule_version=namespace.rule_version,
        apply=namespace.apply,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
