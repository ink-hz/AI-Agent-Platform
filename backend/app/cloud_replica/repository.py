from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
import hashlib
import hmac
import json
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.fleet.catalog import AgentCatalog
from app.fleet.repository import DailyUsage, UsageRecord, UsageSnapshot
from app.observability.models import (
    AgentSummary,
    AttachmentSummary,
    FlywheelFilters,
    FlywheelOverview,
    ImprovementItem,
    Page,
    RuntimeObservation,
    SessionDetail,
    SessionFilters,
    SessionSummary,
    TraceDetail,
    TurnDetail,
)
from app.observability.repository import ObservabilityReadError

from .crypto import FieldCipher, ReplicaCryptoError


_SESSION_COLUMNS = """
session_key, agent_id, source_kind, channel, created_at, last_active_at,
generation_sequence, display_payload, payload_nonce, payload_sha256
""".strip()
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _b64(value: bytes | memoryview) -> str:
    return base64.urlsafe_b64encode(bytes(value)).rstrip(b"=").decode("ascii")


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    raise ValueError


def _calendar_year_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


class ReplicaObservabilityRepository:
    def __init__(
        self,
        database_url: str,
        *,
        cipher: FieldCipher,
        connect: Callable[..., Any] = psycopg.connect,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        stale_seconds: int = 900,
        catalog: AgentCatalog | None = None,
    ):
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

    def check_schema(self) -> None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    """
                    select
                      to_regclass('platform_replica.sessions') as sessions,
                      to_regclass('platform_replica.generations') as generations
                    """
                ).fetchone()
            if not row or row["sessions"] is None or row["generations"] is None:
                raise ValueError
        except Exception:
            raise ObservabilityReadError("replica schema unavailable") from None

    def _latest_success(self) -> datetime | None:
        with self._connection() as connection, connection.cursor() as cursor:
            row = cursor.execute(
                "select max(committed_at) as last_success_at from platform_replica.generations"
            ).fetchone()
        return row["last_success_at"] if row else None

    def deployment_status(self) -> dict[str, Any]:
        try:
            last_success = self._latest_success()
            if last_success is None:
                freshness = "unavailable"
            elif self._now() - last_success > self._stale_after:
                freshness = "stale"
            else:
                freshness = "current"
            return {
                "mode": "cloud-replica",
                "read_only": True,
                "auth": "ssh-tunnel",
                "freshness": freshness,
                "last_success_at": last_success,
            }
        except Exception:
            raise ObservabilityReadError("replica status unavailable") from None

    def _rows(self, *, session_key: str | None = None) -> list[dict[str, Any]]:
        sql = f"select {_SESSION_COLUMNS} from platform_replica.sessions"
        params: tuple[Any, ...] = ()
        if session_key is not None:
            sql += " where session_key = %s"
            params = (session_key,)
        sql += " order by last_active_at desc, session_key"
        with self._connection() as connection, connection.cursor() as cursor:
            return list(cursor.execute(sql, params).fetchall())

    def _decrypt(self, row: dict[str, Any]) -> dict[str, Any]:
        encrypted = {
            "nonce": _b64(row["payload_nonce"]),
            "ciphertext": _b64(row["display_payload"]),
        }
        plaintext = self._cipher.decrypt(
            encrypted, f"1:session:{row['session_key']}"
        )
        if not hmac.compare_digest(
            hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
            row["payload_sha256"],
        ):
            raise ValueError
        record = json.loads(plaintext)
        if (
            not isinstance(record, dict)
            or record.get("kind") != "session"
            or record.get("key") != row["session_key"]
            or record.get("agent_id") != row["agent_id"]
            or record.get("source_kind") != row["source_kind"]
            or record.get("channel") != row["channel"]
            or _time(record.get("created_at")) != _time(row["created_at"])
            or _time(record.get("last_active_at")) != _time(row["last_active_at"])
        ):
            raise ValueError
        return record

    def _records(self, *, session_key: str | None = None) -> list[dict[str, Any]]:
        try:
            return [self._decrypt(row) for row in self._rows(session_key=session_key)]
        except (ReplicaCryptoError, OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            raise ObservabilityReadError("replica read failed") from None
        except Exception:
            raise ObservabilityReadError("replica read failed") from None

    def _status_freshness(self) -> str:
        return "stale" if self.deployment_status()["freshness"] == "stale" else "fresh"

    @staticmethod
    def _latest_outcome(record: dict[str, Any]) -> str | None:
        for turn in reversed(record.get("turns", [])):
            if turn.get("outcome"):
                return turn["outcome"]
        return None

    def _summary(
        self, record: dict[str, Any], deployment: dict[str, Any] | None = None
    ) -> SessionSummary:
        deployment = deployment or self.deployment_status()
        title = record.get("title") or {}
        return SessionSummary(
            session_key=record["key"],
            agent_id=record["agent_id"],
            source_kind=record["source_kind"],
            channel=record.get("channel") or "unknown",
            title=title.get("text"),
            created_at=_time(record["created_at"]),
            last_active_at=_time(record["last_active_at"]),
            turn_count=len(record.get("turns", [])),
            feedback_count=0,
            review_count=0,
            latest_outcome=self._latest_outcome(record),
            source_synced_at=deployment["last_success_at"],
            participant_count=1,
            primary_sender_name=record.get("primary_sender_name"),
            primary_sender_department=record.get("primary_sender_department"),
            sender_identity_status=(
                "resolved" if record.get("primary_sender_name") else "unavailable"
            ),
            freshness="stale" if deployment["freshness"] == "stale" else "fresh",
        )

    def list_sessions(
        self, filters: SessionFilters, limit: int, offset: int
    ) -> Page[SessionSummary]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ObservabilityReadError("replica read failed")
        records = self._records()
        if not filters.agent_id:
            business_ids = set(self._catalog.ids_for_visibility("business"))
            records = [record for record in records if record["agent_id"] in business_ids]
        if filters.sentiment or filters.review_status:
            records = []
        filtered = []
        query = filters.query.casefold() if filters.query else None
        for record in records:
            if filters.agent_id and record["agent_id"] != filters.agent_id:
                continue
            if filters.source_kind and record["source_kind"] != filters.source_kind:
                continue
            if filters.channel and record.get("channel") != filters.channel:
                continue
            if filters.outcome and self._latest_outcome(record) != filters.outcome:
                continue
            active_at = _time(record["last_active_at"])
            if filters.date_from and active_at < filters.date_from:
                continue
            if filters.date_to and active_at > filters.date_to:
                continue
            if query:
                searchable = [str((record.get("title") or {}).get("text") or "")]
                for turn in record.get("turns", []):
                    searchable.extend(
                        (
                            str((turn.get("question") or {}).get("text") or ""),
                            str((turn.get("answer") or {}).get("text") or ""),
                        )
                    )
                if not any(query in value.casefold() for value in searchable):
                    continue
            filtered.append(record)
        filtered.sort(
            key=lambda record: (_time(record["last_active_at"]), record["key"]),
            reverse=True,
        )
        deployment = self.deployment_status()
        return Page[SessionSummary](
            items=[
                self._summary(record, deployment)
                for record in filtered[offset : offset + limit]
            ],
            total=len(filtered),
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _attachment(
        value: dict[str, Any], turn_key: str, index: int, session_active_at: datetime
    ) -> AttachmentSummary:
        direction = value.get("direction")
        is_input = direction in {"source", "incoming"}
        archive = {
            "archived": "available",
            "pending": "pending",
            "failed": "failed",
            "expired": "expired",
        }.get(value.get("archive_status"), "source_unavailable")
        delivery = {
            "pending": "pending",
            "delivered": "delivered",
            "failed": "failed",
        }.get(value.get("delivery_status"), "not_applicable")
        return AttachmentSummary(
            attachment_id=uuid5(NAMESPACE_URL, f"replica:{turn_key}:{index}"),
            direction="user_input" if is_input else "agent_output",
            display_name=value.get("display_label") or f"附件 {index}",
            mime_type=value.get("mime_type"),
            size_bytes=None,
            received_or_generated_at=_time(value["occurred_at"]),
            archive_status=archive,
            delivery_status=delivery,
            expires_at=_calendar_year_after(session_active_at),
            safe_category=value.get("category"),
            size_bucket=value.get("size_bucket"),
            content_available=False,
        )

    def _turn(
        self, record: dict[str, Any], turn: dict[str, Any]
    ) -> TurnDetail:
        attachments = [
            self._attachment(value, turn["key"], index, _time(record["last_active_at"]))
            for index, value in enumerate(turn.get("attachments", []), start=1)
        ]
        trace = turn.get("trace")
        return TurnDetail(
            turn_key=turn["key"],
            session_key=record["key"],
            agent_id=record["agent_id"],
            source_kind=record["source_kind"],
            turn_index=turn["turn_index"],
            question=(turn.get("question") or {}).get("text", ""),
            answer=(turn.get("answer") or {}).get("text", ""),
            created_at=_time(turn["created_at"]),
            question_at=_time(turn["created_at"]),
            question_time_status="estimated",
            answer_time_status="unavailable",
            trace_key=turn["key"] if trace else None,
            outcome=turn.get("outcome"),
            fallback_used=bool(turn.get("fallback_used")),
            duration_ms=turn.get("duration_ms"),
            sources=[],
            evidence=[],
            evidence_availability="restricted",
            input_attachments=[item for item in attachments if item.direction == "user_input"],
            output_attachments=[item for item in attachments if item.direction == "agent_output"],
            sender_name=record.get("primary_sender_name"),
            sender_department=record.get("primary_sender_department"),
            sender_identity_status=(
                "resolved" if record.get("primary_sender_name") else "unavailable"
            ),
        )

    def get_session(self, session_key: str) -> SessionDetail | None:
        records = self._records(session_key=session_key)
        if not records:
            return None
        record = records[0]
        deployment = self.deployment_status()
        return SessionDetail(
            **self._summary(record, deployment).model_dump(),
            turns=[self._turn(record, turn) for turn in record.get("turns", [])],
        )

    def _find_turn(self, turn_key: str):
        for record in self._records():
            for turn in record.get("turns", []):
                if turn.get("key") == turn_key:
                    return record, turn
        return None

    def get_trace(self, turn_key: str) -> TraceDetail | None:
        found = self._find_turn(turn_key)
        if found is None:
            return None
        record, turn = found
        trace = turn.get("trace")
        if not trace:
            return None
        started = _time(turn["created_at"])
        duration = trace.get("duration_ms")
        return TraceDetail(
            trace_key=turn["key"],
            turn_key=turn["key"],
            agent_id=record["agent_id"],
            source_kind=record["source_kind"],
            status=trace.get("status") or "unknown",
            started_at=started,
            completed_at=(started + timedelta(milliseconds=duration))
            if isinstance(duration, int)
            else None,
            duration_ms=duration,
            engine=trace.get("engine"),
            backend=trace.get("backend"),
            model=trace.get("model_family"),
            input_tokens=trace.get("input_tokens"),
            output_tokens=trace.get("output_tokens"),
            cost_usd=trace.get("cost_usd"),
            error_class=trace.get("error_class"),
            error_message=None,
            detail_availability="unavailable",
            source_synced_at=self._latest_success(),
            details={"tool_categories": trace.get("tool_categories", [])},
            steps=[],
        )

    def list_agents(self) -> tuple[AgentSummary, ...]:
        records = self._records()
        latest_success = self._latest_success()
        freshness = self._status_freshness()
        result = []
        for profile in self._catalog.all_profiles():
            owned = [record for record in records if record["agent_id"] == profile.id]
            source = owned[0]["source_kind"] if owned else (
                "fae" if profile.id == "ai-fae-agent" else "admin" if profile.id == "ai-admin-agent" else "metabot"
            )
            result.append(
                AgentSummary(
                    id=profile.id,
                    name=profile.name,
                    domain=profile.domain,
                    description=profile.description,
                    glyph=profile.glyph,
                    accent=profile.accent,
                    visibility=profile.visibility,
                    source_kind=source,
                    deployment="Cloud replica",
                    session_count=len(owned),
                    total_turns=sum(len(record.get("turns", [])) for record in owned),
                    last_activity_at=max(
                        (_time(record["last_active_at"]) for record in owned),
                        default=None,
                    ),
                    last_synced_at=latest_success,
                    freshness=freshness,
                )
            )
        return tuple(sorted(result, key=lambda item: (-item.total_turns, item.id)))

    def get_agent(self, agent_id: str) -> AgentSummary | None:
        return next((agent for agent in self.list_agents() if agent.id == agent_id), None)

    def get_latest_runtime_observation(
        self, agent_id: str
    ) -> RuntimeObservation | None:
        for record in self._records():
            if record["agent_id"] != agent_id:
                continue
            for turn in reversed(record.get("turns", [])):
                trace = turn.get("trace")
                if trace:
                    return RuntimeObservation(
                        agent_id=agent_id,
                        source_kind=record["source_kind"],
                        engine=trace.get("engine"),
                        backend=trace.get("backend"),
                        model=trace.get("model_family"),
                        observed_at=_time(turn["created_at"]),
                    )
        return None

    def get_flywheel_overview(self) -> FlywheelOverview:
        return FlywheelOverview(
            feedback_total=0,
            negative_feedback=0,
            pending_reviews=0,
            evaluation_candidates=0,
            knowledge_tasks=0,
            qa_candidates=0,
        )

    def list_improvement_items(
        self, filters: FlywheelFilters, limit: int, offset: int
    ) -> Page[ImprovementItem]:
        del filters
        return Page[ImprovementItem](items=[], total=0, limit=limit, offset=offset)

    def get_sync_status(self) -> tuple:
        return ()

    def usage_snapshot(self) -> UsageSnapshot:
        records = self._records()
        now = self._now()
        by_agent: dict[str, list[dict[str, Any]]] = {}
        daily: dict[tuple[str, date], int] = {}
        for record in records:
            by_agent.setdefault(record["agent_id"], []).append(record)
            for turn in record.get("turns", []):
                day = _time(turn["created_at"]).astimezone(_SHANGHAI).date()
                daily[(record["agent_id"], day)] = daily.get((record["agent_id"], day), 0) + 1
        usage = []
        today = now.astimezone(_SHANGHAI).date()
        last_success = self._latest_success()
        for agent_id, sessions in by_agent.items():
            turns = [turn for session in sessions for turn in session.get("turns", [])]
            latest_session = max(sessions, key=lambda item: _time(item["last_active_at"]))
            recent_turn = max(turns, key=lambda item: _time(item["created_at"]), default=None)
            usage.append(
                UsageRecord(
                    bot_id=agent_id,
                    total_conversations=len(turns),
                    conversations_last_7d=sum(
                        count for (candidate, day), count in daily.items()
                        if candidate == agent_id and today - timedelta(days=6) <= day <= today
                    ),
                    conversations_previous_7d=sum(
                        count for (candidate, day), count in daily.items()
                        if candidate == agent_id and today - timedelta(days=13) <= day < today - timedelta(days=6)
                    ),
                    last_activity_at=_time(latest_session["last_active_at"]),
                    recent_summary=(recent_turn.get("question") or {}).get("text")
                    if recent_turn
                    else None,
                    session_count=len(sessions),
                    last_synced_at=last_success,
                )
            )
        trend = tuple(
            DailyUsage(bot_id=agent_id, date=day, conversations=count)
            for (agent_id, day), count in sorted(daily.items(), key=lambda item: item[0][1])
            if today - timedelta(days=6) <= day <= today
        )
        return UsageSnapshot(records=tuple(usage), trend=trend, checked_at=now)


class ReplicaFlywheelRepository:
    def __init__(self, repository: ReplicaObservabilityRepository):
        self._repository = repository

    def fetch_usage(self) -> UsageSnapshot:
        return self._repository.usage_snapshot()
