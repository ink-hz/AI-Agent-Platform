from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)


TERMINAL_MISSION_STATUSES = frozenset(
    {"completed", "partially_completed", "failed", "cancelled", "interrupted"}
)
TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
_MISSION_STATUSES = frozenset(
    {
        "planning",
        "delegated",
        "synthesizing",
        *TERMINAL_MISSION_STATUSES,
    }
)
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class MissionRepositoryError(RuntimeError):
    """Stable persistence failure without protected values or SQL details."""

    def __init__(self, message: str = "mission repository unavailable") -> None:
        super().__init__(message)


class MissionRepositoryConflict(MissionRepositoryError):
    def __init__(self) -> None:
        super().__init__("mission repository conflict")


class MissionRepositoryNotFound(MissionRepositoryError):
    def __init__(self) -> None:
        super().__init__("mission not found")


@dataclass(frozen=True)
class MissionRecord:
    mission_id: UUID
    owner_internal_user_id: UUID
    client_request_id: UUID
    mode: Literal["brain", "direct_agent"]
    direct_agent_id: str | None
    status: str
    cancel_requested: bool
    row_version: int
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None
    prompt: str = field(repr=False)


@dataclass(frozen=True)
class MissionEvent:
    event_id: UUID
    mission_id: UUID
    run_id: UUID | None
    seq: int
    event_type: str
    payload: dict[str, object] = field(repr=False)
    created_at: datetime = field(compare=True)


@dataclass(frozen=True)
class MissionRun:
    run_id: UUID
    mission_id: UUID
    task_id: UUID | None
    phase: str
    agent_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    input_payload: dict[str, object] = field(repr=False)
    output_payload: dict[str, object] | None = field(default=None, repr=False)


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID required")
    return value


def _require_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("payload required")
    return value


def _require_agent_id(value: object) -> str:
    if not isinstance(value, str) or _AGENT_ID.fullmatch(value) is None:
        raise ValueError("Agent ID invalid")
    return value


def _require_expected_mission(
    status: str | None, row_version: int | None
) -> None:
    if status is None and row_version is None:
        return
    if (
        status not in _MISSION_STATUSES
        or isinstance(row_version, bool)
        or not isinstance(row_version, int)
        or row_version < 0
    ):
        raise ValueError("expected Mission state invalid")


def _message_subject(mission_id: UUID, message_id: UUID) -> str:
    return f"mission:{mission_id}:message:{message_id}:content"


def _task_subject(mission_id: UUID, task_id: UUID) -> str:
    return f"mission:{mission_id}:task:{task_id}:objective"


def _run_subject(mission_id: UUID, run_id: UUID, field_name: str) -> str:
    return f"mission:{mission_id}:run:{run_id}:{field_name}"


def _event_subject(mission_id: UUID, event_id: UUID) -> str:
    return f"mission:{mission_id}:event:{event_id}:payload"


class MissionRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self.content_codec = content_codec

    def __repr__(self) -> str:
        return (
            "MissionRepository(control_database_url=<redacted>, "
            f"environment={self.environment!r}, content_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    @staticmethod
    def _owned_mission_for_update(
        cursor: Any, internal_user_id: UUID, mission_id: UUID
    ) -> dict[str, Any]:
        row = cursor.execute(
            "select mission_id,mode,direct_agent_id,status,row_version "
            "from platform_control.missions where mission_id=%s "
            "and owner_internal_user_id=%s for update",
            (mission_id, internal_user_id),
        ).fetchone()
        if row is None:
            raise MissionRepositoryNotFound()
        return row

    def _mission_from_row(self, row: dict[str, Any]) -> MissionRecord:
        value = self.content_codec.unseal_json(
            _message_subject(row["mission_id"], row["message_id"]),
            SealedContent(
                bytes(row["content_ciphertext"]), row["encryption_key_version"]
            ),
        )
        if set(value) != {"text"} or not isinstance(value["text"], str):
            raise ContentCryptoError("content decrypt failed")
        return MissionRecord(
            mission_id=row["mission_id"],
            owner_internal_user_id=row["owner_internal_user_id"],
            client_request_id=row["client_request_id"],
            mode=row["mode"],
            direct_agent_id=row["direct_agent_id"],
            status=row["status"],
            cancel_requested=row["cancel_requested"],
            row_version=row["row_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            terminal_at=row["terminal_at"],
            prompt=value["text"],
        )

    def create_mission(
        self,
        internal_user_id: UUID,
        client_request_id: UUID,
        prompt: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionRecord:
        _require_uuid(internal_user_id)
        _require_uuid(client_request_id)
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.encode("utf-8")) > 32 * 1024
        ):
            raise ValueError("Mission prompt invalid")
        if mode == "brain":
            if direct_agent_id is not None:
                raise ValueError("Brain Mission cannot name a direct Agent")
            status = "planning"
        elif mode == "direct_agent":
            direct_agent_id = _require_agent_id(direct_agent_id)
            status = "delegated"
        else:
            raise ValueError("Mission mode invalid")

        mission_id = uuid4()
        message_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                _message_subject(mission_id, message_id), {"text": prompt}
            )
            with self._connection() as connection, connection.cursor() as cursor:
                inserted = cursor.execute(
                    "insert into platform_control.missions "
                    "(mission_id,owner_internal_user_id,client_request_id,mode,"
                    "direct_agent_id,status) values (%s,%s,%s,%s,%s,%s) "
                    "on conflict (owner_internal_user_id,client_request_id) "
                    "do nothing returning created_at,updated_at",
                    (
                        mission_id,
                        internal_user_id,
                        client_request_id,
                        mode,
                        direct_agent_id,
                        status,
                    ),
                ).fetchone()
                if inserted is not None:
                    cursor.execute(
                        "insert into platform_control.mission_messages "
                        "(message_id,mission_id,seq,role,content_ciphertext,"
                        "encryption_key_version) values (%s,%s,1,'user',%s,%s)",
                        (
                            message_id,
                            mission_id,
                            sealed.ciphertext,
                            sealed.key_version,
                        ),
                    )
                    return MissionRecord(
                        mission_id=mission_id,
                        owner_internal_user_id=internal_user_id,
                        client_request_id=client_request_id,
                        mode=mode,
                        direct_agent_id=direct_agent_id,
                        status=status,
                        cancel_requested=False,
                        row_version=0,
                        created_at=inserted["created_at"],
                        updated_at=inserted["updated_at"],
                        terminal_at=None,
                        prompt=prompt,
                    )

                existing = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.owner_internal_user_id=%s and m.client_request_id=%s "
                    "for update of m",
                    (internal_user_id, client_request_id),
                ).fetchone()
                if existing is None:
                    raise MissionRepositoryError()
                replay = self._mission_from_row(existing)
                if (
                    replay.prompt != prompt
                    or replay.mode != mode
                    or replay.direct_agent_id != direct_agent_id
                ):
                    raise MissionRepositoryConflict()
                return replay
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    def mission_for_owner(
        self, internal_user_id: UUID, mission_id: UUID
    ) -> MissionRecord:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.mission_id=%s and m.owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
            if row is None:
                raise MissionRepositoryNotFound()
            return self._mission_from_row(row)
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    def list_missions_for_owner(
        self, internal_user_id: UUID, *, limit: int = 50
    ) -> tuple[MissionRecord, ...]:
        _require_uuid(internal_user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("Mission list limit invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(
                    "select m.*,message.message_id,message.content_ciphertext,"
                    "message.encryption_key_version from platform_control.missions m "
                    "join platform_control.mission_messages message "
                    "on message.mission_id=m.mission_id and message.seq=1 "
                    "where m.owner_internal_user_id=%s "
                    "order by m.created_at desc,m.mission_id desc limit %s",
                    (internal_user_id, limit),
                ).fetchall()
            return tuple(self._mission_from_row(row) for row in rows)
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    @staticmethod
    def _append_event_locked(
        cursor: Any,
        mission_id: UUID,
        run_id: UUID | None,
        event_id: UUID,
        event_type: str,
        sealed: SealedContent,
    ) -> dict[str, Any]:
        sequence = cursor.execute(
            "select coalesce(max(seq),0)+1 as next_seq "
            "from platform_control.mission_events where mission_id=%s",
            (mission_id,),
        ).fetchone()["next_seq"]
        inserted = cursor.execute(
            "insert into platform_control.mission_events "
            "(event_id,mission_id,run_id,seq,event_type,payload_ciphertext,"
            "encryption_key_version) values (%s,%s,%s,%s,%s,%s,%s) "
            "returning created_at",
            (
                event_id,
                mission_id,
                run_id,
                sequence,
                event_type,
                sealed.ciphertext,
                sealed.key_version,
            ),
        ).fetchone()
        return {"seq": sequence, "created_at": inserted["created_at"]}

    def append_event(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        event_type: str,
        payload: dict[str, object],
        *,
        run_id: UUID | None = None,
    ) -> MissionEvent:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        if run_id is not None:
            _require_uuid(run_id)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        _require_payload(payload)
        event_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), payload
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                if run_id is not None:
                    run = cursor.execute(
                        "select status from platform_control.mission_runs "
                        "where mission_id=%s and run_id=%s for update",
                        (mission_id, run_id),
                    ).fetchone()
                    if run is None:
                        raise MissionRepositoryNotFound()
                    if run["status"] in TERMINAL_RUN_STATUSES:
                        raise MissionRepositoryConflict()
                inserted = self._append_event_locked(
                    cursor, mission_id, run_id, event_id, event_type, sealed
                )
            return MissionEvent(
                event_id=event_id,
                mission_id=mission_id,
                run_id=run_id,
                seq=inserted["seq"],
                event_type=event_type,
                payload=payload,
                created_at=inserted["created_at"],
            )
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    def _run_from_row(self, row: dict[str, Any]) -> MissionRun:
        input_payload = self.content_codec.unseal_json(
            _run_subject(row["mission_id"], row["run_id"], "input"),
            SealedContent(
                bytes(row["input_ciphertext"]), row["encryption_key_version"]
            ),
        )
        output_payload = None
        if row["output_ciphertext"] is not None:
            output_payload = self.content_codec.unseal_json(
                _run_subject(row["mission_id"], row["run_id"], "output"),
                SealedContent(
                    bytes(row["output_ciphertext"]),
                    row["output_encryption_key_version"],
                ),
            )
        return MissionRun(
            run_id=row["run_id"],
            mission_id=row["mission_id"],
            task_id=row["task_id"],
            phase=row["phase"],
            agent_id=row["agent_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            input_payload=input_payload,
            output_payload=output_payload,
        )

    def _existing_phase_run(
        self,
        cursor: Any,
        mission_id: UUID,
        phase: str,
        *,
        agent_id: str,
        input_payload: dict[str, object],
        objective: str | None,
        event_type: str,
        event_payload: dict[str, object],
    ) -> MissionRun | None:
        rows = cursor.execute(
            "select run_row.*,task.objective_ciphertext,"
            "task.encryption_key_version as objective_key_version,"
            "initial_event.event_id as initial_event_id,"
            "initial_event.event_type as initial_event_type,"
            "initial_event.payload_ciphertext as initial_event_ciphertext,"
            "initial_event.encryption_key_version as initial_event_key_version "
            "from platform_control.mission_runs run_row "
            "left join platform_control.mission_tasks task "
            "on task.mission_id=run_row.mission_id "
            "and task.task_id=run_row.task_id "
            "left join lateral (select event_id,event_type,payload_ciphertext,"
            "encryption_key_version from platform_control.mission_events "
            "where mission_id=run_row.mission_id and run_id=run_row.run_id "
            "order by seq limit 1) initial_event on true "
            "where run_row.mission_id=%s and run_row.phase=%s "
            "order by run_row.created_at,run_row.run_id for update of run_row",
            (mission_id, phase),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise MissionRepositoryConflict()
        row = rows[0]
        recovered = self._run_from_row(row)
        recovered_objective = None
        if recovered.task_id is not None:
            objective_value = self.content_codec.unseal_json(
                _task_subject(mission_id, recovered.task_id),
                SealedContent(
                    bytes(row["objective_ciphertext"]),
                    row["objective_key_version"],
                ),
            )
            if (
                set(objective_value) != {"text"}
                or not isinstance(objective_value["text"], str)
            ):
                raise ContentCryptoError("content decrypt failed")
            recovered_objective = objective_value["text"]
        if row["initial_event_id"] is None:
            raise MissionRepositoryError()
        recovered_event = self.content_codec.unseal_json(
            _event_subject(mission_id, row["initial_event_id"]),
            SealedContent(
                bytes(row["initial_event_ciphertext"]),
                row["initial_event_key_version"],
            ),
        )
        if (
            recovered.agent_id != agent_id
            or recovered.input_payload != input_payload
            or recovered_objective != objective
            or row["initial_event_type"] != event_type
            or recovered_event != event_payload
        ):
            raise MissionRepositoryConflict()
        return recovered

    def create_run(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        *,
        phase: Literal["planning", "professional", "synthesis", "direct"],
        agent_id: str,
        input_payload: dict[str, object],
        event_type: str,
        event_payload: dict[str, object],
        objective: str | None = None,
        expected_mission_status: str | None = None,
        expected_row_version: int | None = None,
    ) -> MissionRun:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        agent_id = _require_agent_id(agent_id)
        _require_payload(input_payload)
        _require_payload(event_payload)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        _require_expected_mission(
            expected_mission_status, expected_row_version
        )
        if phase in {"planning", "synthesis"}:
            if agent_id != "agent-brain-bot" or objective is not None:
                raise ValueError("Brain run shape invalid")
            task_id = None
        elif phase in {"professional", "direct"}:
            if (
                agent_id == "agent-brain-bot"
                or not isinstance(objective, str)
                or not objective.strip()
            ):
                raise ValueError("Professional run shape invalid")
            task_id = uuid4()
        else:
            raise ValueError("Mission run phase invalid")

        run_id = uuid4()
        event_id = uuid4()
        try:
            sealed_input = self.content_codec.seal_json(
                _run_subject(mission_id, run_id, "input"), input_payload
            )
            sealed_event = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), event_payload
            )
            sealed_objective = (
                self.content_codec.seal_json(
                    _task_subject(mission_id, task_id), {"text": objective}
                )
                if task_id is not None
                else None
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                recovered = self._existing_phase_run(
                    cursor,
                    mission_id,
                    phase,
                    agent_id=agent_id,
                    input_payload=input_payload,
                    objective=objective,
                    event_type=event_type,
                    event_payload=event_payload,
                )
                if recovered is not None:
                    return recovered
                if expected_mission_status is not None and (
                    mission["status"] != expected_mission_status
                    or mission["row_version"] != expected_row_version
                ):
                    raise MissionRepositoryConflict()
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                if phase == "direct" and (
                    mission["mode"] != "direct_agent"
                    or mission["direct_agent_id"] != agent_id
                ):
                    raise MissionRepositoryConflict()
                if phase == "professional" and mission["mode"] != "brain":
                    raise MissionRepositoryConflict()
                if task_id is not None:
                    if cursor.execute(
                        "select task_id from platform_control.mission_tasks "
                        "where mission_id=%s for update",
                        (mission_id,),
                    ).fetchone() is not None:
                        raise MissionRepositoryConflict()
                    cursor.execute(
                        "insert into platform_control.mission_tasks "
                        "(task_id,mission_id,agent_id,objective_ciphertext,"
                        "encryption_key_version,status) "
                        "values (%s,%s,%s,%s,%s,'queued')",
                        (
                            task_id,
                            mission_id,
                            agent_id,
                            sealed_objective.ciphertext,
                            sealed_objective.key_version,
                        ),
                    )
                inserted = cursor.execute(
                    "insert into platform_control.mission_runs "
                    "(run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
                    "encryption_key_version) values (%s,%s,%s,%s,%s,'queued',%s,%s) "
                    "returning created_at,updated_at",
                    (
                        run_id,
                        mission_id,
                        task_id,
                        phase,
                        agent_id,
                        sealed_input.ciphertext,
                        sealed_input.key_version,
                    ),
                ).fetchone()
                if phase in {"professional", "direct"}:
                    next_status = "delegated"
                elif phase == "synthesis":
                    next_status = "synthesizing"
                else:
                    next_status = mission["status"]
                cursor.execute(
                    "update platform_control.missions set status=%s,row_version="
                    "row_version+1,updated_at=now() where mission_id=%s",
                    (next_status, mission_id),
                )
                self._append_event_locked(
                    cursor,
                    mission_id,
                    run_id,
                    event_id,
                    event_type,
                    sealed_event,
                )
            return MissionRun(
                run_id=run_id,
                mission_id=mission_id,
                task_id=task_id,
                phase=phase,
                agent_id=agent_id,
                status="queued",
                created_at=inserted["created_at"],
                updated_at=inserted["updated_at"],
                started_at=None,
                terminal_at=None,
                input_payload=input_payload,
            )
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    def complete_run(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        run_id: UUID,
        *,
        status: Literal["completed", "failed", "cancelled", "interrupted"],
        output_payload: dict[str, object],
        event_type: str,
        event_payload: dict[str, object],
        mission_status: str | None = None,
        expected_mission_status: str | None = None,
        expected_row_version: int | None = None,
    ) -> MissionRun:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        _require_uuid(run_id)
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError("Mission run terminal status invalid")
        if mission_status is not None and mission_status not in _MISSION_STATUSES:
            raise ValueError("Mission status invalid")
        _require_expected_mission(
            expected_mission_status, expected_row_version
        )
        _require_payload(output_payload)
        _require_payload(event_payload)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Mission event type invalid")
        event_id = uuid4()
        try:
            sealed_output = self.content_codec.seal_json(
                _run_subject(mission_id, run_id, "output"), output_payload
            )
            sealed_event = self.content_codec.seal_json(
                _event_subject(mission_id, event_id), event_payload
            )
            with self._connection() as connection, connection.cursor() as cursor:
                mission = self._owned_mission_for_update(
                    cursor, internal_user_id, mission_id
                )
                if expected_mission_status is not None and (
                    mission["status"] != expected_mission_status
                    or mission["row_version"] != expected_row_version
                ):
                    raise MissionRepositoryConflict()
                if mission["status"] in TERMINAL_MISSION_STATUSES:
                    raise MissionRepositoryConflict()
                run = cursor.execute(
                    "select run_id,mission_id,task_id,phase,agent_id,status,"
                    "input_ciphertext,encryption_key_version,created_at,updated_at,"
                    "started_at,terminal_at from platform_control.mission_runs "
                    "where mission_id=%s and run_id=%s for update",
                    (mission_id, run_id),
                ).fetchone()
                if run is None:
                    raise MissionRepositoryNotFound()
                if run["status"] in TERMINAL_RUN_STATUSES:
                    raise MissionRepositoryConflict()
                updated = cursor.execute(
                    "update platform_control.mission_runs set status=%s,"
                    "output_ciphertext=%s,output_encryption_key_version=%s,"
                    "updated_at=now(),terminal_at=now() where run_id=%s "
                    "returning updated_at,terminal_at",
                    (
                        status,
                        sealed_output.ciphertext,
                        sealed_output.key_version,
                        run_id,
                    ),
                ).fetchone()
                if run["task_id"] is not None:
                    cursor.execute(
                        "update platform_control.mission_tasks set status=%s,"
                        "updated_at=now(),terminal_at=now() where mission_id=%s "
                        "and task_id=%s",
                        (status, mission_id, run["task_id"]),
                    )
                if mission_status is not None:
                    terminal = mission_status in TERMINAL_MISSION_STATUSES
                    cursor.execute(
                        "update platform_control.missions set status=%s,"
                        "row_version=row_version+1,updated_at=now(),"
                        "terminal_at=case when %s then now() else null end "
                        "where mission_id=%s",
                        (mission_status, terminal, mission_id),
                    )
                self._append_event_locked(
                    cursor,
                    mission_id,
                    run_id,
                    event_id,
                    event_type,
                    sealed_event,
                )
                input_value = self.content_codec.unseal_json(
                    _run_subject(mission_id, run_id, "input"),
                    SealedContent(
                        bytes(run["input_ciphertext"]), run["encryption_key_version"]
                    ),
                )
            return MissionRun(
                run_id=run_id,
                mission_id=mission_id,
                task_id=run["task_id"],
                phase=run["phase"],
                agent_id=run["agent_id"],
                status=status,
                created_at=run["created_at"],
                updated_at=updated["updated_at"],
                started_at=run["started_at"],
                terminal_at=updated["terminal_at"],
                input_payload=input_value,
                output_payload=output_payload,
            )
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None

    def events_after(
        self,
        internal_user_id: UUID,
        mission_id: UUID,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> tuple[MissionEvent, ...]:
        _require_uuid(internal_user_id)
        _require_uuid(mission_id)
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ValueError("Mission event cursor invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                owned = cursor.execute(
                    "select mission_id from platform_control.missions "
                    "where mission_id=%s and owner_internal_user_id=%s",
                    (mission_id, internal_user_id),
                ).fetchone()
                if owned is None:
                    raise MissionRepositoryNotFound()
                rows = cursor.execute(
                    "select event.event_id,event.mission_id,event.run_id,event.seq,"
                    "event.event_type,event.payload_ciphertext,"
                    "event.encryption_key_version,event.created_at "
                    "from platform_control.mission_events event "
                    "join platform_control.missions mission "
                    "on mission.mission_id=event.mission_id "
                    "where event.mission_id=%s and mission.owner_internal_user_id=%s "
                    "and event.seq>%s order by event.seq limit %s",
                    (mission_id, internal_user_id, after, limit),
                ).fetchall()
            events: list[MissionEvent] = []
            for row in rows:
                payload = self.content_codec.unseal_json(
                    _event_subject(row["mission_id"], row["event_id"]),
                    SealedContent(
                        bytes(row["payload_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                events.append(
                    MissionEvent(
                        event_id=row["event_id"],
                        mission_id=row["mission_id"],
                        run_id=row["run_id"],
                        seq=row["seq"],
                        event_type=row["event_type"],
                        payload=payload,
                        created_at=row["created_at"],
                    )
                )
            return tuple(events)
        except MissionRepositoryError:
            raise
        except (
            ContentCryptoError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
            psycopg.Error,
        ):
            raise MissionRepositoryError() from None
