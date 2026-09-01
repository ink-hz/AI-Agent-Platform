from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import jcs

from .contract import MAX_REPORT_BYTES, ReportContractError, load_report_document

_ACTOR = re.compile(r"^(?:corp:[0-9a-f-]{36}|service:fae-report-importer)$")


class ReportImportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReportImportResult:
    report_id: str
    report_version: int
    status: str
    result: str
    payload_digest: str


class FaeReportImporter:
    def __init__(self, repository) -> None:
        self._repository = repository

    def import_path(self, path: Path, *, actor: str) -> ReportImportResult:
        if not _ACTOR.fullmatch(actor):
            raise ReportImportError("report_actor_invalid")
        if not path.is_absolute():
            raise ReportImportError("report_path_invalid")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ReportImportError("report_path_invalid") from error
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ReportImportError("report_path_invalid")
        if metadata.st_size > MAX_REPORT_BYTES:
            raise ReportImportError("report_limit_exceeded")
        try:
            payload = path.read_bytes()
            report = load_report_document(payload)
        except (OSError, ReportContractError) as error:
            raise ReportImportError(str(error)) from error
        canonical = jcs.canonicalize(report.model_dump(mode="json"))
        digest = hashlib.sha256(canonical).hexdigest()
        stored = self._repository.import_report(
            report, canonical, digest=digest, actor=actor
        )
        return ReportImportResult(
            report_id=report.report_id,
            report_version=report.report_version,
            status=report.status,
            result=stored["result"],
            payload_digest=digest,
        )
