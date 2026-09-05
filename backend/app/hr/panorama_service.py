# ruff: noqa: TRY004
from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid5

from app.agent_brain.conversation_repository import (
    ConversationRepositoryConflict,
    ConversationRepositoryError,
)

from .panorama_models import (
    CreatePanoramaRun,
    CreateTalentSource,
    PanoramaReport,
    PanoramaRun,
    TalentInsightVersion,
    TalentSource,
)
from .panorama_repository import PanoramaConflict, PanoramaUnavailable

_PANORAMA_CONVERSATION_TITLE = "全景分析"


class PanoramaCommandRepository(Protocol):
    def create_source(self, command: CreateTalentSource) -> TalentSource: ...

    def list_sources(
        self, owner_id: UUID, *, include_inactive: bool = False, limit: int = 100
    ) -> tuple[TalentSource, ...]: ...

    def create_run(self, command: CreatePanoramaRun) -> PanoramaRun: ...

    def run(self, owner_id: UUID, run_id: UUID) -> PanoramaRun: ...

    def list_insights(
        self, owner_id: UUID, *, limit: int = 100
    ) -> tuple[TalentInsightVersion, ...]: ...

    def report(self, owner_id: UUID, insight_version_id: UUID) -> PanoramaReport: ...

    def relevant_insights(
        self, owner_id: UUID, query: str, position_id: UUID, *, limit: int = 5
    ) -> tuple[TalentInsightVersion, ...]: ...


class PanoramaService:
    def __init__(
        self,
        repository: PanoramaCommandRepository,
        *,
        coordinator: object | None = None,
        conversations: object | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        for method in (
            "create_source",
            "list_sources",
            "create_run",
            "run",
            "list_insights",
            "report",
            "relevant_insights",
        ):
            if not callable(getattr(repository, method, None)):
                raise ValueError("panorama repository invalid")
        if uuid_factory is not None and not callable(uuid_factory):
            raise ValueError("panorama UUID factory invalid")
        if coordinator is not None and any(
            not callable(getattr(coordinator, name, None))
            for name in ("preflight", "submit")
        ):
            raise ValueError("panorama coordinator invalid")
        if conversations is not None and not callable(
            getattr(conversations, "ensure_direct_conversation_shell", None)
        ):
            raise ValueError("panorama conversations invalid")
        self._repository = repository
        self._coordinator = coordinator
        self._conversations = conversations
        self._uuid_factory = uuid_factory

    def _resource_id(self, owner_id: UUID, request_id: UUID, operation: str) -> UUID:
        if self._uuid_factory is not None:
            generated = self._uuid_factory()
            if not isinstance(generated, UUID):
                raise ValueError("panorama UUID factory invalid")
            return generated
        return uuid5(owner_id, f"hr-panorama:{operation}:{request_id}")

    def add_company(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        canonical_name: str,
        aliases: tuple[str, ...],
        approved_urls: tuple[str, ...],
    ) -> TalentSource:
        company_key = _company_key(canonical_name)
        return self._repository.create_source(
            CreateTalentSource(
                source_id=self._resource_id(owner_id, request_id, "source"),
                owner_id=owner_id,
                client_request_id=request_id,
                company_key=company_key,
                canonical_name=canonical_name,
                aliases=aliases,
                approved_urls=approved_urls,
                active=True,
            )
        )

    def list_companies(
        self, owner_id: UUID, *, include_inactive: bool = False, limit: int = 100
    ) -> tuple[TalentSource, ...]:
        return self._repository.list_sources(
            owner_id, include_inactive=include_inactive, limit=limit
        )

    def start_run(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        source_ids: tuple[UUID, ...],
        conversation_id: UUID | None = None,
    ) -> PanoramaRun:
        if self._coordinator is None:
            raise PanoramaUnavailable("panorama runtime unavailable")
        self._coordinator.preflight(owner_id, source_ids)
        run_id = uuid5(owner_id, f"hr-panorama:run:{request_id}")
        selected_conversation_id = conversation_id
        if selected_conversation_id is None:
            if self._conversations is None:
                raise PanoramaUnavailable("panorama conversations unavailable")
            shell_request_id = uuid5(run_id, "hr-panorama:conversation-shell:v1")
            try:
                shell = self._conversations.ensure_direct_conversation_shell(
                    owner_id,
                    shell_request_id,
                    direct_agent_id="hr-bot",
                    title=_PANORAMA_CONVERSATION_TITLE,
                )
            except ConversationRepositoryConflict:
                raise PanoramaConflict("panorama conversation conflict") from None
            except ConversationRepositoryError:
                raise PanoramaUnavailable("panorama conversation unavailable") from None
            if (
                getattr(shell, "owner_internal_user_id", None) != owner_id
                or getattr(shell, "started_by_client_request_id", None)
                != shell_request_id
                or getattr(shell, "mode", None) != "direct_agent"
                or getattr(shell, "direct_agent_id", None) != "hr-bot"
                or getattr(shell, "status", None) != "active"
                or getattr(shell, "title", None) != _PANORAMA_CONVERSATION_TITLE
                or not isinstance(getattr(shell, "conversation_id", None), UUID)
            ):
                raise PanoramaConflict("panorama conversation mismatch")
            selected_conversation_id = shell.conversation_id
        run = self._repository.create_run(
            CreatePanoramaRun(
                run_id=run_id,
                owner_id=owner_id,
                client_request_id=request_id,
                selected_source_ids=source_ids,
                conversation_id=selected_conversation_id,
            )
        )
        if self._coordinator is not None:
            self._coordinator.submit(run.run_id)
        return run

    def report(self, owner_id: UUID, insight_version_id: UUID) -> PanoramaReport:
        return self._repository.report(owner_id, insight_version_id)

    def list_reports(
        self, owner_id: UUID, *, limit: int = 100
    ) -> tuple[TalentInsightVersion, ...]:
        return self._repository.list_insights(owner_id, limit=limit)

    def run_status(self, owner_id: UUID, run_id: UUID) -> PanoramaRun:
        return self._repository.run(owner_id, run_id)

    def relevant_insights(
        self, owner_id: UUID, query: str, position_id: UUID, *, limit: int = 5
    ) -> tuple[TalentInsightVersion, ...]:
        return self._repository.relevant_insights(
            owner_id, query, position_id, limit=limit
        )


def _company_key(canonical_name: str) -> str:
    if not isinstance(canonical_name, str):
        raise ValueError("company name invalid")
    normalized = unicodedata.normalize("NFKC", canonical_name).strip().casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"company-{digest}"


__all__ = ["PanoramaCommandRepository", "PanoramaService"]
