from __future__ import annotations

from typing import Protocol
from uuid import UUID

from .panorama_models import PanoramaReport, PublishedPanorama


class PanoramaReadRepository(Protocol):
    def current_publication(self) -> PublishedPanorama | None: ...

    def list_publications(
        self, *, limit: int = 100
    ) -> tuple[PublishedPanorama, ...]: ...

    def publication(self, publication_id: UUID) -> PublishedPanorama: ...

    def report(self, owner_id: UUID, insight_version_id: UUID) -> PanoramaReport: ...


class PanoramaService:
    """Read-only business boundary for quality-gated panorama publications."""

    def __init__(self, repository: PanoramaReadRepository) -> None:
        for method in (
            "current_publication",
            "list_publications",
            "publication",
            "report",
        ):
            if not callable(getattr(repository, method, None)):
                raise TypeError("panorama repository invalid")
        self._repository = repository

    def current_report(self) -> PanoramaReport | None:
        publication = self._repository.current_publication()
        return None if publication is None else self._published_report(publication)

    def list_reports(self, *, limit: int = 100) -> tuple[PanoramaReport, ...]:
        return tuple(
            self._published_report(publication)
            for publication in self._repository.list_publications(limit=limit)
        )

    def report(self, publication_id: UUID) -> PanoramaReport:
        if not isinstance(publication_id, UUID):
            raise TypeError("panorama publication identifier invalid")
        return self._published_report(self._repository.publication(publication_id))

    def _published_report(self, publication: PublishedPanorama) -> PanoramaReport:
        report = self._repository.report(
            publication.owner_id, publication.insight_version_id
        )
        return PanoramaReport(
            insight=report.insight,
            sources=report.sources,
            snapshots=report.snapshots,
            publication=publication,
        )


__all__ = ["PanoramaReadRepository", "PanoramaService"]
