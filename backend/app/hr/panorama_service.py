from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .panorama_evidence import EvidenceError
from .panorama_models import PanoramaReport, PublishedPanorama, SourceCollectionAttempt
from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MIME = re.compile(r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}\Z")


class PanoramaEvidenceArchive(Protocol):
    def read(self, sha256: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PanoramaEvidenceFile:
    sha256: str
    mime: str
    body: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sha256, str)
            or _SHA256.fullmatch(self.sha256) is None
            or not isinstance(self.mime, str)
            or _MIME.fullmatch(self.mime) is None
            or not isinstance(self.body, bytes)
            or len(self.body) > 10 * 1024 * 1024
        ):
            raise ValueError("panorama evidence file invalid")


class PanoramaReadRepository(Protocol):
    def current_publication(self) -> PublishedPanorama | None: ...

    def list_publications(
        self, *, limit: int = 100
    ) -> tuple[PublishedPanorama, ...]: ...

    def publication(self, publication_id: UUID) -> PublishedPanorama: ...

    def report(self, owner_id: UUID, insight_version_id: UUID) -> PanoramaReport: ...

    def source_attempts_for_production_batch(
        self, owner_id: UUID, batch_id: UUID
    ) -> tuple[SourceCollectionAttempt, ...]: ...


class PanoramaService:
    """Read-only business boundary for quality-gated panorama publications."""

    def __init__(
        self,
        repository: PanoramaReadRepository,
        *,
        evidence_archive: PanoramaEvidenceArchive | None = None,
    ) -> None:
        for method in (
            "current_publication",
            "list_publications",
            "publication",
            "report",
            "source_attempts_for_production_batch",
        ):
            if not callable(getattr(repository, method, None)):
                raise TypeError("panorama repository invalid")
        self._repository = repository
        self._evidence_archive = evidence_archive

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

    def evidence_file(self, publication_id: UUID, sha256: str) -> PanoramaEvidenceFile:
        if (
            not isinstance(publication_id, UUID)
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
        ):
            raise TypeError("panorama evidence identifier invalid")
        if self._evidence_archive is None:
            raise PanoramaUnavailable("panorama evidence archive unavailable")
        publication = self._repository.publication(publication_id)
        attempts = self._repository.source_attempts_for_production_batch(
            publication.owner_id, publication.batch_id
        )
        match = next(
            (attempt for attempt in attempts if attempt.evidence_sha256 == sha256),
            None,
        )
        if match is None or match.evidence_mime is None:
            raise PanoramaNotFound("panorama evidence not found")
        mime = match.evidence_mime.split(";", 1)[0].strip().lower()
        try:
            body = self._evidence_archive.read(sha256)
        except (EvidenceError, OSError):
            raise PanoramaUnavailable("panorama evidence unavailable") from None
        if match.evidence_size_bytes != len(body):
            raise PanoramaUnavailable("panorama evidence size mismatch")
        return PanoramaEvidenceFile(sha256=sha256, mime=mime, body=body)

    def _published_report(self, publication: PublishedPanorama) -> PanoramaReport:
        report = self._repository.report(
            publication.owner_id, publication.insight_version_id
        )
        attempts = self._repository.source_attempts_for_production_batch(
            publication.owner_id, publication.batch_id
        )
        return PanoramaReport(
            insight=report.insight,
            sources=report.sources,
            snapshots=report.snapshots,
            publication=publication,
            evidence_attempts=attempts,
        )


__all__ = [
    "PanoramaEvidenceArchive",
    "PanoramaEvidenceFile",
    "PanoramaReadRepository",
    "PanoramaService",
]
