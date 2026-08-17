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
        if (
            not isinstance(value, dict)
            or value.get("kind") != row["projection_kind"]
            or value.get("key") != row["record_key"]
            or value.get("agent_id") != row["agent_id"]
        ):
            raise ValueError
        return value


class ReplicaReviewRepository(_ProjectionReader):
    def overview(self, *, agent_id: str | None = None) -> dict:
        issues = self._records("review_issue_projection", agent_id)
        inbox = self._records("review_inbox_projection", agent_id)
        dispositions: dict[str, int] = {}
        for issue in issues:
            status = str(issue.get("status") or "unknown")
            dispositions[status] = dispositions.get(status, 0) + 1
        return {
            "feedback_rows": sum(int(item["feedback_count"]) for item in inbox),
            "negative_rows": sum(int(item["feedback_count"]) for item in inbox),
            "negative_turns": len(inbox),
            "positive_rows": 0,
            "dispositions": dispositions,
            "statuses": dispositions,
            "issue_total": len(issues),
        }

    def list_inbox(
        self, *, agent_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        records = self._records("review_inbox_projection", agent_id)
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
        self, *, agent_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        return [
            self._issue(item)
            for item in self._records("review_issue_projection", agent_id)[
                offset : offset + limit
            ]
        ]

    @staticmethod
    def _issue(item: dict) -> dict:
        status = item.get("status") or "unknown"
        return {
            "id": item["key"],
            "agent_id": item["agent_id"],
            "title": (item.get("title") or {}).get("text", ""),
            "priority": item.get("priority"),
            "failure_layer": item.get("failure_layer"),
            "owner": item.get("owner_display"),
            "disposition": status,
            "updated_at": item["updated_at"],
            "progress": {"status": status, "missing_gates": []},
            "linked_turn_count": item.get("linked_turn_count", 0),
            "replica_read_only": True,
        }

    def get_issue_detail(self, issue_id: UUID) -> dict | None:
        for item in self._records("review_issue_projection"):
            if item["key"] == str(issue_id):
                issue = self._issue(item)
                return {
                    "issue": issue,
                    "links": [],
                    "evidence": [],
                    "replays": [],
                    "events": [],
                    "progress": issue["progress"],
                    "replica_read_only": True,
                }
        return None

    def get_turn_summaries(self, _turn_keys: list[str]) -> list[dict]:
        return []


class ReplicaOperationsRepository(_ProjectionReader):
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
                event_family="execution", severity=value["severity"],
                status="historical", title=value["event_type"],
                summary=(value.get("summary") or {}).get("text", ""),
                source_kind="cloud-replica", occurred_at=occurred_at,
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
            if item.severity in {"attention", "critical"}
        )

    def usage_leaders(
        self,
        date_from: datetime,
        date_to: datetime,
        agent_visibility: str = "business",
    ) -> tuple[UsageLeader, ...]:
        # A cloud conversation count is derived from the replicated Sessions
        # themselves: one Session created inside the window is one new
        # conversation. The local poller's usage occurrences are not replicated,
        # so counting them here would always report zero.
        allowed = set(self._catalog.ids_for_visibility(agent_visibility))
        try:
            with self._connection() as connection:
                rows = list(
                    connection.execute(
                        "select agent_id, count(*) as conversations "
                        "from platform_replica.sessions "
                        "where created_at >= %s and created_at <= %s "
                        "group by agent_id",
                        (date_from, date_to),
                    ).fetchall()
                )
        except Exception as error:
            raise ReviewRepositoryError("replica usage unavailable") from error
        leaders = [
            UsageLeader(
                agent_id=str(row["agent_id"]),
                agent_name=self._catalog.profile(
                    str(row["agent_id"]), str(row["agent_id"])
                ).name,
                conversations=int(row["conversations"]),
            )
            for row in rows
            if str(row["agent_id"]) in allowed
            and not self._catalog.is_excluded(str(row["agent_id"]))
        ]
        leaders.sort(key=lambda item: (-item.conversations, item.agent_id))
        return tuple(leaders)

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
