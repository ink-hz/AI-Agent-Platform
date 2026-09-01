from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from .repository import StoredReport


class FaeReportService:
    def __init__(
        self,
        repository,
        *,
        latest_source_sync: Callable[[], datetime | None] | None = None,
    ) -> None:
        self._repository = repository
        self._latest_source_sync = latest_source_sync or (lambda: None)

    @staticmethod
    def _document(value: StoredReport | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, StoredReport):
            document = value.report.model_dump(mode="json")
            for finding in document.get("findings", []):
                finding["linked_issue_ids"] = sorted(
                    set(finding.get("linked_issue_ids", ()))
                    | set(value.active_issue_links.get(finding["finding_id"], ()))
                )
            document["publication"] = {
                "payload_digest": value.payload_digest,
                "imported_at": value.imported_at.isoformat(),
            }
            return document
        document = dict(value)
        document["publication"] = None
        return document

    def _decorate(self, value: StoredReport | dict[str, Any]) -> dict[str, Any]:
        document = self._document(value)
        latest = self._latest_source_sync()
        cutoff = datetime.fromisoformat(
            str(document["data_cutoff_at"]).replace("Z", "+00:00")
        )
        document["latest_source_sync_at"] = latest.isoformat() if latest else None
        document["currentness"] = (
            "source_updated" if latest is not None and latest > cutoff else "current"
        )
        return document

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        return [self._decorate(value) for value in self._repository.list_reports(status=status)]

    def latest(self) -> dict[str, Any] | None:
        values = self._repository.list_reports(status="ready")
        return self._decorate(values[0]) if values else None

    def detail(self, report_id: str, version: int | None = None) -> dict[str, Any] | None:
        value = self._repository.get_report(report_id, version)
        return self._decorate(value) if value is not None else None
