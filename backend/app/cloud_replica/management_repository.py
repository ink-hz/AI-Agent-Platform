from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from typing import Any, Callable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.fleet.catalog import AgentCatalog
from app.observability.models import Page
from app.operations.models import EventFilters, OperationalEvent, RunHealth, UsageLeader
from app.review.repository import ReviewRepositoryError

from .crypto import FieldCipher


_PLATFORM_EVENT_AGENT_ID = "platform"


def _b64(value: bytes | memoryview) -> str:
    return base64.urlsafe_b64encode(bytes(value)).rstrip(b"=").decode("ascii")


class _ProjectionReader:
    def __init__(
        self,
        database_url: str,
        *,
        cipher: FieldCipher,
        connect: Callable[..., Any] = psycopg.connect,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        stale_seconds: int = 900,
        catalog: AgentCatalog | None = None,
    ) -> None:
        self._database_url = database_url
        self._cipher = cipher
        self._connect = connect
        self._now = now
        self._stale_after = timedelta(seconds=stale_seconds)
        self._catalog = catalog or AgentCatalog.default()

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
            row_factory=dict_row,
        )

    @property
    def stale_after_seconds(self) -> float:
        return self._stale_after.total_seconds()

    def _records(self, kind: str, agent_id: str | None = None) -> list[dict]:
        if agent_id is not None and self._catalog.is_excluded(agent_id):
            return []
        sql = (
            "select projection_kind,record_key,agent_id,occurred_at,"
            "display_payload,payload_nonce,payload_sha256 "
            "from platform_replica.management_projections "
            "where projection_kind=%s"
        )
        params: list[object] = [kind]
        if agent_id is not None:
            sql += " and agent_id=%s"
            params.append(agent_id)
        sql += " order by occurred_at desc,record_key"
        try:
            with self._connection() as connection:
                freshness = connection.execute(
                    "select max(committed_at) as committed_at "
                    "from platform_replica.generations"
                ).fetchone()
                committed_at = freshness["committed_at"] if freshness else None
                if committed_at is None or self._now() - committed_at > self._stale_after:
                    raise ReviewRepositoryError("replica management projection stale")
                rows = list(connection.execute(sql, tuple(params)).fetchall())
            return [
                value
                for value in (self._decrypt(row) for row in rows)
                if not self._catalog.is_excluded(
                    str(value.get("agent_id") or "")
                )
            ]
        except ReviewRepositoryError:
            raise
        except Exception as error:
            raise ReviewRepositoryError(
                "replica management projection unavailable"
            ) from error

    def _decrypt(self, row: dict[str, Any]) -> dict:
        encrypted = {
            "nonce": _b64(row["payload_nonce"]),
            "ciphertext": _b64(row["display_payload"]),
        }
        plaintext = self._cipher.decrypt(
            encrypted,
            f"2:{row['projection_kind']}:{row['record_key']}",
        )
        if not hmac.compare_digest(
            hashlib.sha256(plaintext.encode()).hexdigest(),
            row["payload_sha256"],
        ):
            raise ValueError
        value = json.loads(plaintext)
        payload_agent_id = value.get("agent_id") if isinstance(value, dict) else None
        indexed_agent_matches = payload_agent_id == row["agent_id"] or (
            row["projection_kind"] == "operation_event_projection"
            and payload_agent_id is None
            and row["agent_id"] == _PLATFORM_EVENT_AGENT_ID
        )
        if (
            not isinstance(value, dict)
            or value.get("kind") != row["projection_kind"]
            or value.get("key") != row["record_key"]
            or not indexed_agent_matches
        ):
            raise ValueError
        return value


class ReplicaReviewRepository(_ProjectionReader):
    def agent_issue_scope_valid(self, agent_id: str) -> bool:
        issues = self._records("review_issue_projection", agent_id)
        return not issues or any(issue.get("scope_valid") is True for issue in issues)

    def _valid_issues(self, agent_id: str | None = None) -> list[dict]:
        return [
            issue
            for issue in self._records("review_issue_projection", agent_id)
            if issue.get("scope_valid") is True
        ]

    def overview(self, *, agent_id: str | None = None) -> dict:
        all_issues = self._records("review_issue_projection", agent_id)
        issues = [issue for issue in all_issues if issue.get("scope_valid") is True]
        totals = self._records("review_feedback_totals_projection", agent_id)
        dispositions: dict[str, int] = {}
        for issue in issues:
            status = str(issue.get("status") or "unknown")
            dispositions[status] = dispositions.get(status, 0) + 1
        overview = {
            "dispositions": dispositions,
            "statuses": dispositions,
            "issue_total": len(issues),
            "quarantined_issue_count": len(all_issues) - len(issues),
        }
        if not totals:
            # The inbox only holds untriaged negative feedback, so it must never
            # stand in for the totals: a fully triaged Agent would report zero
            # feedback. Report the absence instead of a wrong zero.
            overview.update(
                feedback_rows=None,
                negative_rows=None,
                negative_turns=None,
                positive_rows=None,
                feedback_totals_status="unavailable",
            )
            return overview
        overview.update(
            feedback_rows=sum(int(item["feedback_rows"]) for item in totals),
            negative_rows=sum(int(item["negative_rows"]) for item in totals),
            negative_turns=sum(int(item["negative_turns"]) for item in totals),
            positive_rows=sum(int(item["positive_rows"]) for item in totals),
            feedback_totals_status="resolved",
        )
        return overview

    def list_inbox(
        self, *, agent_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        records = self._records("review_inbox_projection", agent_id)
        if any(item.get("scope_valid") is not True for item in records):
            raise ReviewRepositoryError("replica inbox scope unavailable")
        return [
            {
                "agent_id": item["agent_id"],
                "turn_key": item["turn_key"],
                "feedback_count": item["feedback_count"],
                "feedback_keys": [],
                "first_feedback_at": item["first_feedback_at"],
            }
            for item in records[offset : offset + limit]
        ]

    def list_issues(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        disposition: str | None = None,
    ) -> list[dict]:
        return self.list_issue_page(
            agent_id=agent_id, limit=limit, offset=offset,
            status=status, disposition=disposition,
        )["items"]


    def list_issue_page(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        disposition: str | None = None,
        priority: str | None = None,
        failure_layer: str | None = None,
        owner: str | None = None,
        query: str | None = None,
        created_after: datetime | None = None,
    ) -> dict:
        records = self._valid_issues(agent_id)
        if disposition is not None:
            records = [
                item for item in records
                if str(item.get("status") or "unknown") == disposition
            ]
        if status == "open":
            records = [
                item for item in records
                if str(item.get("status") or "unknown")
                not in {"closed", "duplicate", "not_actionable", "wont_fix"}
            ]
        elif status is not None:
            # Cloud projections contain disposition, not lifecycle. A lifecycle
            # filter therefore has no representable matches on the replica.
            records = []
        if priority is not None:
            records = [item for item in records if item.get("priority") == priority]
        if failure_layer is not None:
            records = [
                item for item in records if item.get("failure_layer") == failure_layer
            ]
        if owner is not None:
            records = [item for item in records if item.get("owner_display") == owner]
        if query:
            needle = query.casefold()
            records = [
                item for item in records
                if needle in str((item.get("title") or {}).get("text") or "").casefold()
            ]
        if created_after is not None:
            if any(item.get("created_at") is None for item in records):
                raise ReviewRepositoryError("replica issue created filter unavailable")
            records = [
                item for item in records
                if datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                >= created_after
            ]
        total = len(records)
        items = [self._issue(item) for item in records[offset : offset + limit]]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    @staticmethod
    def _issue(item: dict) -> dict:
        status = item.get("status") or "unknown"
        progress = item.get("progress")
        if not isinstance(progress, dict):
            progress = {"status": status, "missing_gates": []}
        return {
            "id": item["key"],
            "agent_id": item["agent_id"],
            "title": (item.get("title") or {}).get("text", ""),
            "priority": item.get("priority"),
            "failure_layer": item.get("failure_layer"),
            "owner": item.get("owner_display"),
            "disposition": status,
            "detail_schema_version": item.get("detail_schema_version"),
            "origin_turn_key": item.get("origin_turn_key"),
            "root_cause": item.get("root_cause") or "",
            "impact_scope": item.get("impact_scope") or "",
            "secondary_layers": item.get("secondary_layers") or [],
            "created_at": item.get("created_at"),
            "updated_at": item["updated_at"],
            "progress": progress,
            "linked_turn_count": item.get("linked_turn_count", 0),
            "replica_read_only": True,
        }

    def get_issue_detail(self, issue_id: UUID) -> dict | None:
        for item in self._valid_issues():
            if item["key"] == str(issue_id):
                issue = self._issue(item)
                detailed = item.get("detail_schema_version") == 1
                return {
                    "issue": issue,
                    "links": item.get("links", []) if detailed else None,
                    "evidence": item.get("evidence", []) if detailed else None,
                    "replays": item.get("replays", []) if detailed else None,
                    "events": item.get("events", []) if detailed else None,
                    "availability": {
                        section: "available" if detailed else "unavailable"
                        for section in ("links", "evidence", "replays", "events")
                    },
                    "progress": issue["progress"],
                    "replica_read_only": True,
                    "projection_scope_valid": True,
                }
        return None

    def get_turn_summaries(self, turn_keys: list[str]) -> list[dict]:
        requested = list(dict.fromkeys(turn_keys))
        if not requested:
            return []
        issues = self._valid_issues()
        if any("linked_turn_keys" not in issue for issue in issues):
            raise ReviewRepositoryError("replica turn governance unavailable")
        by_turn = {
            turn_key: issue
            for issue in issues
            for turn_key in issue.get("linked_turn_keys", [])
            if turn_key in requested
        }
        return [
            {
                "turn_key": turn_key,
                "issue_id": by_turn[turn_key]["key"],
                "status": "unknown",
                "missing_gates": None,
                "latest_valid_replay_id": None,
            }
            for turn_key in requested if turn_key in by_turn
        ]


class ReplicaFaeReportRepository(_ProjectionReader):
    _KINDS = (
        "fae_report_header_projection",
        "fae_report_metric_projection",
        "fae_report_finding_projection",
        "fae_report_recommendation_projection",
    )

    def latest_source_sync(self) -> datetime | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select max(upper_watermark) as upper_watermark "
                    "from platform_replica.generations"
                ).fetchone()
            return row["upper_watermark"] if row else None
        except Exception as error:
            raise ReviewRepositoryError(
                "replica report freshness unavailable"
            ) from error

    def _groups(self) -> dict[tuple[str, int], dict[str, list[dict]]]:
        groups: dict[tuple[str, int], dict[str, list[dict]]] = {}
        for kind in self._KINDS:
            for record in self._records(kind, "ai-fae-agent"):
                key = (str(record["report_id"]), int(record["report_version"]))
                groups.setdefault(key, {}).setdefault(kind, []).append(record)
        return groups

    @staticmethod
    def _assemble(key: tuple[str, int], group: dict[str, list[dict]]) -> dict:
        headers = group.get("fae_report_header_projection", [])
        if len(headers) != 1:
            raise ReviewRepositoryError("report_projection_incomplete")
        document = dict(headers[0]["payload"])
        counts = document.pop("counts")
        mappings = (
            ("metrics", "fae_report_metric_projection", "metric_id"),
            ("findings", "fae_report_finding_projection", "finding_id"),
            (
                "recommendations",
                "fae_report_recommendation_projection",
                "recommendation_id",
            ),
        )
        for field, kind, identity in mappings:
            items = [dict(item["payload"]) for item in group.get(kind, [])]
            if len(items) != int(counts[field]) or len(
                {item[identity] for item in items}
            ) != len(items):
                raise ReviewRepositoryError("report_projection_incomplete")
            document[field] = sorted(items, key=lambda item: str(item[identity]))
        if (document.get("report_id"), int(document.get("report_version", 0))) != key:
            raise ReviewRepositoryError("report_projection_incomplete")
        return document

    def list_reports(self, *, status: str | None = None) -> list[dict]:
        values = []
        for key, group in self._groups().items():
            try:
                report = self._assemble(key, group)
            except ReviewRepositoryError:
                continue
            if status is None or report.get("status") == status:
                values.append(report)
        return sorted(
            values,
            key=lambda item: (
                str(item.get("data_cutoff_at")),
                int(item["report_version"]),
            ),
            reverse=True,
        )

    def get_report(self, report_id: str, version: int | None = None) -> dict | None:
        candidates = [
            (key, group)
            for key, group in self._groups().items()
            if key[0] == report_id and (version is None or key[1] == version)
        ]
        if not candidates:
            return None
        key, group = max(candidates, key=lambda item: item[0][1])
        return self._assemble(key, group)


class ReplicaOperationsRepository(_ProjectionReader):
    _LEGACY_EVENT_FAMILIES = {
        "new_conversations": "usage",
        "conversation_milestone": "usage",
        "agent_launched": "lifecycle",
        "deployment_updated": "lifecycle",
        "sync_recovered": "recovery",
        "data_access_recovered": "recovery",
        "runtime_recovered": "recovery",
        "remote_sync_unavailable": "data",
        "business_data_unavailable": "data",
        "runtime_degraded": "runtime",
        "runtime_offline": "runtime",
    }

    def __init__(self, *args, usage_reader=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._usage_reader = usage_reader

    @classmethod
    def _legacy_event_family(cls, event_type: str) -> str:
        return cls._LEGACY_EVENT_FAMILIES.get(event_type, "execution")

    def list_events(
        self, filters: EventFilters, limit: int, offset: int
    ) -> Page[OperationalEvent]:
        values = self._records("operation_event_projection", filters.agent_id)
        items: list[OperationalEvent] = []
        for value in values:
            occurred_at = datetime.fromisoformat(
                value["occurred_at"].replace("Z", "+00:00")
            )
            if filters.event_type and value["event_type"] != filters.event_type:
                continue
            if filters.severity and value["severity"] != filters.severity:
                continue
            if filters.date_from and occurred_at < filters.date_from:
                continue
            if filters.date_to and occurred_at > filters.date_to:
                continue
            items.append(OperationalEvent(
                event_id=value["key"], agent_id=value["agent_id"],
                agent_visibility="business", event_type=value["event_type"],
                event_family=(value.get("event_family") or
                              self._legacy_event_family(value["event_type"])),
                severity=value["severity"],
                status=value.get("status") or "historical",
                title=(value.get("title") or {}).get("text") or value["event_type"],
                summary=(value.get("summary") or {}).get("text", ""),
                source_kind=value.get("source_kind") or "cloud-replica",
                occurred_at=occurred_at,
                first_observed_at=occurred_at, last_observed_at=occurred_at,
                facts={}, fingerprint=value["key"],
            ))
        return Page[OperationalEvent](
            items=items[offset : offset + limit], total=len(items),
            limit=limit, offset=offset,
        )

    def list_active_attention(self, _agent_visibility: str):
        return tuple(
            item for item in self.list_events(EventFilters(), 10_000, 0).items
            if item.status == "active"
            and item.severity in {"attention", "critical"}
        )

    def usage_leaders(
        self,
        date_from: datetime,
        date_to: datetime,
        agent_visibility: str = "business",
    ) -> tuple[UsageLeader, ...]:
        if self._usage_reader is None:
            raise ReviewRepositoryError("replica usage unavailable")
        try:
            return tuple(
                self._usage_reader(date_from, date_to, agent_visibility)
            )
        except ReviewRepositoryError:
            raise
        except Exception as error:
            raise ReviewRepositoryError("replica usage unavailable") from error

    def latest_run(self, run_name: str) -> RunHealth | None:
        # The cloud refresh unit is the replica import, not a local poller. A
        # committed generation is a successful refresh; this deliberately does
        # not apply the staleness guard so the Brief can report `stale` instead
        # of failing outright.
        committed_at = self._last_import()
        if committed_at is None:
            return None
        return RunHealth(
            run_name=run_name,
            status="succeeded",
            started_at=committed_at,
            finished_at=committed_at,
        )

    def latest_successful_run(self, run_name: str) -> RunHealth | None:
        return self.latest_run(run_name)

    def _last_import(self) -> datetime | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select max(committed_at) as committed_at "
                    "from platform_replica.generations"
                ).fetchone()
        except Exception as error:
            raise ReviewRepositoryError("replica generation unavailable") from error
        committed_at = row["committed_at"] if row else None
        if committed_at is None:
            return None
        if committed_at.tzinfo is None:
            return committed_at.replace(tzinfo=UTC)
        return committed_at
