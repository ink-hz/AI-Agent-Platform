# ruff: noqa: TRY004
from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid5

from .panorama_models import (
    CreatePanoramaRun,
    CreateTalentSource,
    PanoramaReport,
    PanoramaRun,
    TalentInsightVersion,
    TalentSource,
)


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
        self._repository = repository
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
        conversation_id: UUID,
    ) -> PanoramaRun:
        return self._repository.create_run(
            CreatePanoramaRun(
                run_id=self._resource_id(owner_id, request_id, "run"),
                owner_id=owner_id,
                client_request_id=request_id,
                selected_source_ids=source_ids,
                conversation_id=conversation_id,
            )
        )

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
