from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jcs
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import FaeAnalysisReport, ReportEvidence


class FaeReportRepositoryError(RuntimeError):
    pass


class ReportVersionConflict(FaeReportRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class StoredReport:
    report_pk: str
    report: FaeAnalysisReport
    payload_digest: str
    imported_at: Any
    active_issue_links: dict[str, tuple[str, ...]]


def canonical_report_bytes(report: FaeAnalysisReport) -> bytes:
    return jcs.canonicalize(report.model_dump(mode="json"))


def report_digest(report: FaeAnalysisReport) -> str:
    return hashlib.sha256(canonical_report_bytes(report)).hexdigest()


class PsycopgFaeReportRepository:
    def __init__(
        self, database_url: str, *, connect: Callable[..., Any] = psycopg.connect
    ) -> None:
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=30000",
            row_factory=dict_row,
        )

    @staticmethod
    def _all_evidence(report: FaeAnalysisReport):
        for finding in report.findings:
            for ordinal, evidence in enumerate(finding.evidence_refs):
                yield finding.finding_id, ordinal, evidence
        for case in report.cases:
            for ordinal, evidence in enumerate(case.evidence_refs):
                yield f"case:{case.case_id}", ordinal, evidence

    @staticmethod
    def _resolve_evidence(cursor, evidence: ReportEvidence) -> tuple[str, str | None]:
        queries = {
            "session": "select agent_id,source_kind from platform_read.sessions where session_key=%s",
            "turn": "select agent_id,source_kind from platform_read.turns where turn_key=%s",
            "feedback": "select agent_id,source_kind from platform_read.feedback where feedback_key=%s",
            "issue": "select agent_id,'fae' as source_kind from platform_review.feedback_issues where id=%s::uuid",
        }
        row = cursor.execute(
            queries[evidence.kind], (evidence.canonical_key,)
        ).fetchone()
        if row is None:
            return "unavailable", "not_synced"
        if row["agent_id"] != "ai-fae-agent" or row["source_kind"] != "fae":
            raise FaeReportRepositoryError(
                f"invalid_evidence_scope:{evidence.kind}:{evidence.canonical_key}"
            )
        return "available", None

    def import_report(
        self,
        report: FaeAnalysisReport,
        payload: bytes,
        *,
        digest: str,
        actor: str,
    ) -> dict[str, str]:
        canonical_payload = json.loads(payload)
        try:
            with (
                self._connection() as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                existing = cursor.execute(
                    "select report_pk,payload_digest from platform_fae_reports.reports where report_id=%s and report_version=%s",
                    (report.report_id, report.report_version),
                ).fetchone()
                if existing is not None:
                    if existing["payload_digest"] != digest:
                        raise ReportVersionConflict("report_version_conflict")
                    return {
                        "report_pk": str(existing["report_pk"]),
                        "result": "unchanged",
                    }

                evidence_rows = [
                    (
                        finding_id,
                        ordinal,
                        evidence,
                        *self._resolve_evidence(cursor, evidence),
                    )
                    for finding_id, ordinal, evidence in self._all_evidence(report)
                ]
                row = cursor.execute(
                    """
                    insert into platform_fae_reports.reports
                      (report_id,report_version,report_type,status,title,period_start,
                       period_end,data_cutoff_at,generated_at,imported_by,
                       analysis_version,source_snapshot_at,payload_digest,payload)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    returning report_pk
                    """,
                    (
                        report.report_id,
                        report.report_version,
                        report.report_type,
                        report.status,
                        report.title,
                        report.period.start_at,
                        report.period.end_at,
                        report.data_cutoff_at,
                        report.generated_at,
                        actor,
                        report.analysis_version,
                        report.source.source_snapshot_at,
                        digest,
                        Jsonb(canonical_payload),
                    ),
                ).fetchone()
                report_pk = row["report_pk"]
                for (
                    finding_id,
                    ordinal,
                    evidence,
                    availability,
                    reason,
                ) in evidence_rows:
                    cursor.execute(
                        """
                        insert into platform_fae_reports.report_evidence
                          (report_pk,finding_id,evidence_ordinal,evidence_kind,
                           canonical_key,label,import_availability,import_unavailable_reason)
                        values (%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            report_pk,
                            finding_id,
                            ordinal,
                            evidence.kind,
                            evidence.canonical_key,
                            evidence.label,
                            availability,
                            reason,
                        ),
                    )
                cursor.execute(
                    "insert into platform_fae_reports.report_audit_events (report_pk,event_type,actor,details) values (%s,'report_imported',%s,%s)",
                    (
                        report_pk,
                        actor,
                        Jsonb({"payload_digest": digest, "status": report.status}),
                    ),
                )
                return {"report_pk": str(report_pk), "result": "imported"}
        except (ReportVersionConflict, FaeReportRepositoryError):
            raise
        except Exception as error:
            raise FaeReportRepositoryError("fae_report_import_failed") from error

    def list_reports(self, *, status: str | None = None) -> list[StoredReport]:
        sql = "select * from platform_fae_reports.reports"
        params: tuple[object, ...] = ()
        if status is not None:
            sql += " where status=%s"
            params = (status,)
        sql += " order by data_cutoff_at desc,report_version desc"
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(sql, params).fetchall()
                return [self._stored(cursor, row) for row in rows]
        except Exception as error:
            raise FaeReportRepositoryError("fae_report_read_failed") from error

    def get_report(
        self, report_id: str, version: int | None = None
    ) -> StoredReport | None:
        sql = "select * from platform_fae_reports.reports where report_id=%s"
        params: list[object] = [report_id]
        if version is not None:
            sql += " and report_version=%s"
            params.append(version)
        sql += " order by report_version desc limit 1"
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(sql, tuple(params)).fetchone()
                return self._stored(cursor, row) if row else None
        except Exception as error:
            raise FaeReportRepositoryError("fae_report_read_failed") from error

    @staticmethod
    def _stored(cursor, row: dict[str, Any]) -> StoredReport:
        links = cursor.execute(
            "select finding_id,issue_id from platform_fae_reports.finding_issue_links where report_pk=%s and unlinked_at is null order by finding_id,issue_id",
            (row["report_pk"],),
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for link in links:
            grouped.setdefault(str(link["finding_id"]), []).append(
                str(link["issue_id"])
            )
        return StoredReport(
            report_pk=str(row["report_pk"]),
            report=FaeAnalysisReport.model_validate(row["payload"]),
            payload_digest=str(row["payload_digest"]),
            imported_at=row["imported_at"],
            active_issue_links={key: tuple(value) for key, value in grouped.items()},
        )
