"""Transactional HR work records. Model/network IO never runs under these locks."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.execution_relay.content_crypto import SealedContent

from .types import (
    AuthorizedScope,
    ContextRebuildRequired,
    HrAgentProblem,
    LeaseFence,
    ModelReply,
    ModelRequest,
    RuntimeAction,
    ScopedEntry,
    StoredToolOperation,
    WorkerIdentity,
    WorkPaused,
    canonical_json,
    content_sha256,
    problem,
    validate_contract,
    validate_tool_arguments,
)

DEFAULT_BUDGET = {"model_calls": 32, "total_tokens": 600000, "active_seconds": 900}
DEFAULT_RESERVE = {"model_calls": 2, "total_tokens": 40000, "active_seconds": 60}
EVENT_MESSAGES = {
    "accepted": "工作已受理。",
    "input_changed": "已接收新的工作输入。",
    "started": "开始处理。",
    "progress": "工作进度已保存。",
    "model_committed": "模型步骤已保存。",
    "tool_finished": "工具处理已完成。",
    "tool_error": "资料或操作未能完成，请查看错误类别。",
    "result_saved": "成果已保存。",
    "question_opened": "需要补充信息。",
    "budget_changed": "已追加工作预算。",
    "state_changed": "工作状态已更新。",
    "recovery_started": "正在恢复已保存进度。",
    "context_compacted": "阶段记录已整理。",
}


def _uuid(value):
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise problem("invalid_input") from None


def _refs(values):
    return tuple({canonical_json(ref): ref for ref in values}.values())


def _objects(values):
    return {(v["kind"], v["id"]) for v in values}


class Replayed(dict):
    replayed = True


from .repository_views import RepositoryViewsMixin


class HrAgentRepository(RepositoryViewsMixin):
    def __init__(
        self, connection_factory, codec, *, settings=None, scope_validator=None
    ):
        self.connection_factory = connection_factory
        self.codec = codec
        self.settings = settings
        self.scope_validator = scope_validator
        self.release_provider = None
        self.release_validator = None

    @contextmanager
    def transaction(self):
        paused = None
        with (
            self.connection_factory() as connection,
            connection.transaction(),
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            try:
                yield cursor
            except (WorkPaused, ContextRebuildRequired) as error:
                paused = error
        if paused is not None:
            raise paused

    def _seal(self, table, identity, field, value):
        sealed = self.codec.seal_json(f"hr-agent:{table}:{identity}:{field}", value)
        return {field: sealed.ciphertext, field + "_key_version": sealed.key_version}

    def _unseal(self, table, identity, field, row):
        return self.codec.unseal_json(
            f"hr-agent:{table}:{identity}:{field}",
            SealedContent(bytes(row[field]), row[field + "_key_version"]),
        )

    @staticmethod
    def _insert(c, table, values):
        c.execute(
            sql.SQL("INSERT INTO platform_hr_agent.{} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, values)),
                sql.SQL(",").join(sql.Placeholder() for _ in values),
            ),
            tuple(values.values()),
        )

    @staticmethod
    def _update(c, table, identity_name, identity, values):
        c.execute(
            sql.SQL("UPDATE platform_hr_agent.{} SET {} WHERE {}=%s").format(
                sql.Identifier(table),
                sql.SQL(",").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values
                ),
                sql.Identifier(identity_name),
            ),
            (*values.values(), identity),
        )

    def _scope(self, owner, objects, refs, work_id=None):
        if self.scope_validator:
            self.scope_validator(_uuid(owner), tuple(objects), tuple(refs), work_id)
        elif objects or refs:
            raise problem("configuration_unavailable", http_status=503)
        return AuthorizedScope(
            _uuid(owner),
            _uuid(work_id) if work_id else None,
            tuple(objects),
            tuple(refs),
        )

    def _work(self, c, owner, work_id, lock=False):
        c.execute(
            "SELECT * FROM platform_hr_agent.works WHERE owner_id=%s AND work_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (_uuid(owner), _uuid(work_id)),
        )
        row = c.fetchone()
        if row is None:
            raise problem("not_found", http_status=404)
        return row

    def _fence(self, c, fence):
        c.execute(
            "SELECT *,lease_until>clock_timestamp() AS lease_live FROM platform_hr_agent.works WHERE work_id=%s FOR UPDATE",
            (_uuid(fence.work_id),),
        )
        row = c.fetchone()
        if (
            row is None
            or row["state"] != "running"
            or not row["lease_live"]
            or row["input_revision"] != fence.input_revision
            or row["lease_epoch"] != fence.epoch
            or row["lease_owner"] != fence.worker_id
        ):
            raise problem("lease_lost", http_status=409)
        return row

    def _input(self, c, work):
        c.execute(
            "SELECT * FROM platform_hr_agent.inputs WHERE owner_id=%s AND work_id=%s AND revision=%s",
            (work["owner_id"], work["work_id"], work["input_revision"]),
        )
        row = c.fetchone()
        if row["configuration_revision"] != getattr(
            self.settings, "configuration_revision", "a1-test"
        ):
            raise problem("configuration_unavailable", http_status=503)
        if self.release_validator:
            self.release_validator(row)
        elif self.release_provider and any(
            row[k] != v for k, v in self.release_provider().items()
        ):
            raise problem("configuration_unavailable", http_status=503)
        body = self._unseal("inputs", row["input_id"], "sealed_input", row)
        self._scope(
            work["owner_id"], body["objects"], body["references"], work["work_id"]
        )
        return body, row

    def _idempotency(self, c, owner, namespace, key, request):
        identity = f"{owner}:{namespace}:{_uuid(key)}"
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (identity,))
        c.execute(
            "SELECT * FROM platform_hr_agent.operations WHERE owner_id=%s AND namespace=%s AND request_key=%s",
            (_uuid(owner), namespace, str(key)),
        )
        op = c.fetchone()
        if op:
            if op["request_hash"] != content_sha256(request):
                raise problem("idempotency_conflict", http_status=409)
            return op["operation_id"], Replayed(
                self._unseal("operations", op["operation_id"], "sealed_receipt", op)
            )
        op_id = uuid4()
        self._insert(
            c,
            "operations",
            {
                "operation_id": op_id,
                "owner_id": _uuid(owner),
                "namespace": namespace,
                "request_key": str(key),
                "request_hash": content_sha256(request),
                "status": "prepared",
                **self._seal("operations", op_id, "sealed_arguments", request),
            },
        )
        return op_id, None

    def _receipt(self, c, op_id, value):
        self._update(
            c,
            "operations",
            "operation_id",
            op_id,
            {
                "status": "committed",
                **self._seal("operations", op_id, "sealed_receipt", value),
            },
        )
        return copy.deepcopy(value)

    def _event(self, c, work, kind, *, ref=None, error=None, status=None):
        work["last_event_seq"] += 1
        self._update(
            c,
            "works",
            "work_id",
            work["work_id"],
            {"last_event_seq": work["last_event_seq"]},
        )
        self._insert(
            c,
            "events",
            {
                "owner_id": work["owner_id"],
                "work_id": work["work_id"],
                "seq": work["last_event_seq"],
                "input_revision": work["input_revision"],
                "type": kind,
                "state": work["state"],
                "phase": work["phase"],
                "error_code": error["code"] if error else None,
                "error_retryable": error["retryable"] if error else None,
                "tool_status": status,
                "result_ref": Jsonb(ref) if ref else None,
            },
        )

    def _entry(
        self,
        c,
        work,
        kind,
        body,
        *,
        refs=(),
        objects=None,
        attempt_id=None,
        operation_id=None,
        provenance=None,
    ):
        identity = uuid4()
        c.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS seq FROM platform_hr_agent.entries WHERE work_id=%s",
            (work["work_id"],),
        )
        seq = c.fetchone()["seq"]
        values = {
            "entry_id": identity,
            "owner_id": work["owner_id"],
            "work_id": work["work_id"],
            "input_revision": work["input_revision"],
            "seq": seq,
            "kind": kind,
            "objects": Jsonb(list(objects or [])),
            "local_work_id": work["work_id"],
            "source_refs": Jsonb(list(refs)),
            "model_attempt_id": attempt_id,
            "operation_id": operation_id,
            **self._seal("entries", identity, "sealed_body", body),
        }
        if provenance:
            values.update(
                self._seal("entries", identity, "sealed_summary_provenance", provenance)
            )
        self._insert(c, "entries", values)
        self._edges(
            c,
            work["owner_id"],
            "entry",
            str(identity),
            str(work["input_revision"]),
            refs,
        )
        return identity

    def _edges(self, c, owner, kind, identity, revision, refs):
        for ref in _refs(refs):
            c.execute(
                "INSERT INTO platform_hr_agent.reference_edges (owner_id,dependent_kind,dependent_id,dependent_revision,source_kind,source_id,source_revision,source_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    owner,
                    kind,
                    identity,
                    revision,
                    ref["kind"],
                    ref["id"],
                    ref["revision"],
                    ref["sha256"],
                ),
            )

    @staticmethod
    def _initial_checkpoint(input_revision, refs):
        return {
            "revision": 1,
            "input_revision": input_revision,
            "discovery_state": "open",
            "catalog_refs": [],
            "readings": [
                {
                    "ref": r,
                    "total_characters": None,
                    "remaining_ranges": [],
                    "state": "unread",
                }
                for r in refs
                if r["kind"] == "material"
            ],
            "open_questions": [],
            "pending_operation_ids": [],
            "last_note_entry_id": None,
        }

    def _budget(self, profile):
        if self.settings is not None:
            config = self.settings.budget_profile
            if profile != config["id"]:
                raise problem("configuration_unavailable", http_status=503)
            limits = config["limits"]
            reserve = config["reserve"]
        else:
            if profile != "calibration-test":
                raise problem("configuration_unavailable", http_status=503)
            limits = DEFAULT_BUDGET
            reserve = DEFAULT_RESERVE
        return {
            "revision": 1,
            "limits": copy.deepcopy(limits),
            "reserve": copy.deepcopy(reserve),
            "charged_calls": 0,
            "charged_tokens": 0,
            "active_seconds": 0,
            "usage_quality": "reported",
        }

    def _new_input(self, c, work, body):
        identity = uuid4()
        release = (
            self.release_provider()
            if self.release_provider
            else {
                "role_release": "a1-test",
                "knowledge_release": "a1-test",
                "role_manifest_sha": "0" * 64,
            }
        )
        self._insert(
            c,
            "inputs",
            {
                "owner_id": work["owner_id"],
                "work_id": work["work_id"],
                "revision": work["input_revision"],
                "input_id": identity,
                "objects": Jsonb(body["objects"]),
                **release,
                "configuration_revision": getattr(
                    self.settings, "configuration_revision", "a1-test"
                ),
                **self._seal("inputs", identity, "sealed_input", body),
            },
        )
        self._entry(
            c,
            work,
            "user",
            {"body": body["text"]},
            refs=body["references"],
            objects=body["objects"],
        )

    def submit(self, owner_id, request, key):
        request = validate_contract("WorkInput", request)
        owner_id = _uuid(owner_id)
        self._scope(owner_id, request["objects"], request["references"])
        with self.transaction() as c:
            op, replay = self._idempotency(c, owner_id, "POST /works", key, request)
            if replay:
                return replay
            thread = _uuid(request["thread_id"]) if request["thread_id"] else uuid4()
            if request["thread_id"]:
                c.execute(
                    "SELECT thread_id FROM platform_hr_agent.threads WHERE owner_id=%s AND thread_id=%s FOR UPDATE",
                    (owner_id, thread),
                )
                if not c.fetchone():
                    raise problem("not_found", http_status=404)
            else:
                self._insert(
                    c,
                    "threads",
                    {
                        "thread_id": thread,
                        "owner_id": owner_id,
                        **self._seal(
                            "threads",
                            thread,
                            "sealed_title",
                            {"title": request["text"][:120]},
                        ),
                    },
                )
            work_id = uuid4()
            budget = self._budget(request["budget_profile"])
            checkpoint = self._initial_checkpoint(1, request["references"])
            self._insert(
                c,
                "works",
                {
                    "work_id": work_id,
                    "owner_id": owner_id,
                    "thread_id": thread,
                    "state": "queued",
                    **self._seal("works", work_id, "sealed_budget", budget),
                    **self._seal("works", work_id, "sealed_checkpoint", checkpoint),
                },
            )
            work = self._work(c, owner_id, work_id, True)
            self._new_input(c, work, request)
            self._event(c, work, "accepted")
            return self._receipt(c, op, self._view(c, work))

    def _view(self, c, work):
        checkpoint = self._unseal("works", work["work_id"], "sealed_checkpoint", work)
        budget = self._unseal("works", work["work_id"], "sealed_budget", work)
        if checkpoint["last_note_entry_id"]:
            c.execute(
                "SELECT * FROM platform_hr_agent.inputs WHERE owner_id=%s AND work_id=%s AND revision=%s",
                (work["owner_id"], work["work_id"], work["input_revision"]),
            )
            input_row = c.fetchone()
            current = self._unseal(
                "inputs", input_row["input_id"], "sealed_input", input_row
            )
            c.execute(
                "SELECT * FROM platform_hr_agent.entries WHERE owner_id=%s AND work_id=%s AND entry_id=%s",
                (
                    work["owner_id"],
                    work["work_id"],
                    _uuid(checkpoint["last_note_entry_id"]),
                ),
            )
            note = c.fetchone()
            if not note or not self._entry_allowed(c, work, current, note):
                checkpoint["open_questions"] = []
                checkpoint["last_note_entry_id"] = None
        refs = []
        c.execute(
            "SELECT r.result_id,r.current_revision,v.sha256,v.objects FROM platform_hr_agent.results r JOIN platform_hr_agent.result_revisions v ON v.revision_id=r.current_revision WHERE r.owner_id=%s AND r.origin_work_id=%s",
            (work["owner_id"], work["work_id"]),
        )
        for row in c.fetchall():
            ref = {
                "kind": "result",
                "id": str(row["result_id"]),
                "revision": str(row["current_revision"]),
                "sha256": row["sha256"],
            }
            try:
                self._validate_result_sources(c, work["owner_id"], ref, work["work_id"])
                refs.append(ref)
            except HrAgentProblem:
                continue
        question = None
        if work["state"] == "waiting_user":
            c.execute(
                "SELECT entry_id FROM platform_hr_agent.entries WHERE work_id=%s AND input_revision=%s AND kind='question' ORDER BY seq DESC LIMIT 1",
                (work["work_id"], work["input_revision"]),
            )
            row = c.fetchone()
            question = str(row["entry_id"]) if row else None
        for reading in list(checkpoint["readings"]):
            try:
                self._scope(work["owner_id"], [], [reading["ref"]], work["work_id"])
            except HrAgentProblem:
                checkpoint["readings"].remove(reading)
        block_reason = None
        if work["state"] == "blocked":
            c.execute(
                "SELECT error_code FROM platform_hr_agent.events WHERE owner_id=%s AND work_id=%s AND state='blocked' AND error_code IS NOT NULL ORDER BY seq DESC LIMIT 1",
                (work["owner_id"], work["work_id"]),
            )
            cause = c.fetchone()
            block_reason = cause["error_code"] if cause else "configuration_unavailable"
        return {
            "work_id": str(work["work_id"]),
            "thread_id": str(work["thread_id"]),
            "input_revision": work["input_revision"],
            "state": work["state"],
            "phase": work["phase"],
            "answer_state": work["answer_state"],
            "result_refs": refs,
            "pending_question_id": question,
            "block_reason": block_reason,
            "budget": budget,
            "checkpoint": checkpoint,
            "last_event_seq": work["last_event_seq"],
        }

    def get_work(self, owner_id, work_id):
        with self.transaction() as c:
            return self._view(c, self._work(c, owner_id, work_id))

    def append_input(self, owner_id, work_id, request, key):
        request = validate_contract("AppendInput", request)
        self._scope(owner_id, request["objects"], request["references"])
        with self.transaction() as c:
            work = self._work(c, owner_id, work_id, True)
            op, replay = self._idempotency(
                c, owner_id, f"POST /works/{work_id}/inputs", key, request
            )
            if replay:
                return replay
            if work["state"] == "cancelled":
                raise problem("cancelled", http_status=409)
            if work["input_revision"] != request["expected_input_revision"]:
                raise problem(
                    "revision_conflict",
                    http_status=409,
                    details={
                        "conflict_kind": "input_revision",
                        "current_revision": work["input_revision"],
                    },
                )
            if (
                work["state"] == "waiting_user"
                and request["question_id"] != self._view(c, work)["pending_question_id"]
            ):
                raise problem("revision_conflict", http_status=409)
            if work["state"] != "waiting_user" and request["question_id"] is not None:
                raise problem("invalid_input")
            self._charge_time(c, work)
            c.execute(
                "UPDATE platform_hr_agent.model_attempts SET status='superseded' WHERE work_id=%s AND status IN ('prepared','sending')",
                (work["work_id"],),
            )
            work.update(
                input_revision=work["input_revision"] + 1,
                state="queued",
                phase="research",
                lease_epoch=work["lease_epoch"] + 1,
                lease_owner=None,
                lease_until=None,
            )
            changes = {
                k: work[k]
                for k in [
                    "input_revision",
                    "state",
                    "phase",
                    "lease_epoch",
                    "lease_owner",
                    "lease_until",
                ]
            }
            changes.update(
                self._seal(
                    "works",
                    work["work_id"],
                    "sealed_checkpoint",
                    self._initial_checkpoint(
                        work["input_revision"], request["references"]
                    ),
                )
            )
            self._update(c, "works", "work_id", work["work_id"], changes)
            work.update(changes)
            self._new_input(c, work, request)
            self._event(c, work, "input_changed")
            return self._receipt(c, op, self._view(c, work))

    def cancel(self, owner_id, work_id, reason, key):
        with self.transaction() as c:
            work = self._work(c, owner_id, work_id, True)
            op, replay = self._idempotency(
                c, owner_id, f"POST /works/{work_id}/cancel", key, {"reason": reason}
            )
            if replay:
                return replay
            if work["state"] not in ("completed", "cancelled", "failed"):
                self._state(c, work, "cancelled")
                self._event(c, work, "state_changed")
            return self._receipt(c, op, self._view(c, work))

    def extend_budget(self, owner_id, work_id, request, key):
        request = validate_contract("ExtendBudgetInput", request)
        with self.transaction() as c:
            work = self._work(c, owner_id, work_id, True)
            self._input(c, work)
            op, replay = self._idempotency(
                c, owner_id, f"POST /works/{work_id}/budget-extensions", key, request
            )
            if replay:
                return replay
            if work["state"] == "cancelled":
                raise problem("cancelled", http_status=409)
            budget = self._unseal("works", work["work_id"], "sealed_budget", work)
            if budget["revision"] != request["expected_budget_revision"]:
                raise problem(
                    "revision_conflict",
                    http_status=409,
                    details={
                        "conflict_kind": "budget_revision",
                        "current_revision": budget["revision"],
                    },
                )
            for field, value in request["addition"].items():
                budget["limits"][field] += value
            budget["revision"] += 1
            extension = uuid4()
            self._insert(
                c,
                "budget_extensions",
                {
                    "extension_id": extension,
                    "owner_id": work["owner_id"],
                    "work_id": work["work_id"],
                    "budget_revision": budget["revision"],
                    "addition": Jsonb(request["addition"]),
                    **self._seal(
                        "budget_extensions",
                        extension,
                        "reason_cipher",
                        {"reason": request["reason"]},
                    ),
                    "created_by_operation": op,
                },
            )
            self._save_budget(c, work, budget)
            if work["state"] == "waiting_budget":
                self._state(c, work, "queued", phase="research")
            self._event(c, work, "budget_changed")
            return self._receipt(c, op, self._view(c, work))

    def _state(self, c, work, state, **extra):
        if work["state"] == "running" and state != "running":
            self._charge_time(c, work)
        updates = {"state": state, **extra}
        if state != "running":
            updates.update(
                lease_owner=None, lease_until=None, lease_epoch=work["lease_epoch"] + 1
            )
        self._update(c, "works", "work_id", work["work_id"], updates)
        work.update(updates)

    def _save_budget(self, c, work, budget):
        updates = {
            **self._seal("works", work["work_id"], "sealed_budget", budget),
            "budget_revision": budget["revision"],
        }
        self._update(c, "works", "work_id", work["work_id"], updates)
        work.update(updates)

    def claim(self, worker_id, lease_seconds):
        if not worker_id or lease_seconds <= 0:
            raise problem("invalid_input")
        with self.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.works WHERE state='queued' OR (state='running' AND lease_until<clock_timestamp()) ORDER BY created_at,work_id FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            work = c.fetchone()
            if not work:
                return None
            recovering = work["state"] == "running"
            # Charge the unobserved active interval once, bounded by the old lease.
            if recovering:
                self._charge_time(c, work)
            c.execute(
                "UPDATE platform_hr_agent.works SET state='running',lease_epoch=lease_epoch+1,lease_owner=%s,lease_until=clock_timestamp()+(%s*interval '1 second'),last_active_at=clock_timestamp() WHERE work_id=%s RETURNING *",
                (worker_id, lease_seconds, work["work_id"]),
            )
            work = c.fetchone()
            self._event(c, work, "recovery_started" if recovering else "started")
            return LeaseFence(
                work["work_id"], work["input_revision"], work["lease_epoch"], worker_id
            )

    def _charge_time(self, c, work):
        if work["last_active_at"] and work["lease_until"]:
            c.execute(
                "SELECT GREATEST(0,EXTRACT(EPOCH FROM (LEAST(clock_timestamp(),%s)-%s))) AS elapsed",
                (work["lease_until"], work["last_active_at"]),
            )
            budget = self._unseal("works", work["work_id"], "sealed_budget", work)
            budget["active_seconds"] += float(c.fetchone()["elapsed"])
            self._save_budget(c, work, budget)
            c.execute(
                "UPDATE platform_hr_agent.works SET last_active_at=clock_timestamp() WHERE work_id=%s RETURNING last_active_at",
                (work["work_id"],),
            )
            work["last_active_at"] = c.fetchone()["last_active_at"]

    def renew(self, fence, lease_seconds):
        try:
            with self.transaction() as c:
                work = self._fence(c, fence)
                self._charge_time(c, work)
                c.execute(
                    "UPDATE platform_hr_agent.works SET lease_until=clock_timestamp()+(%s*interval '1 second') WHERE work_id=%s",
                    (lease_seconds, work["work_id"]),
                )
                return True
        except HrAgentProblem:
            return False

    def _validate_result_sources(self, c, owner, ref, work_id=None):
        # Exact graph traversal, deduplicated. No current-pointer substitution.
        pending = [ref]
        seen = set()
        while pending:
            current = pending.pop()
            identity = canonical_json(current)
            if identity in seen:
                continue
            seen.add(identity)
            if len(seen) > 10000:
                raise problem("temporarily_unavailable", http_status=503)
            if current["kind"] != "result":
                self._scope(owner, [], [current], work_id)
                continue
            c.execute(
                "SELECT objects,sha256 FROM platform_hr_agent.result_revisions WHERE owner_id=%s AND result_id=%s AND revision_id=%s",
                (owner, _uuid(current["id"]), _uuid(current["revision"])),
            )
            row = c.fetchone()
            if not row:
                raise problem("not_found", http_status=404)
            if row["sha256"] != current["sha256"]:
                raise problem("hash_mismatch", http_status=409)
            self._scope(owner, row["objects"], [], work_id)
            c.execute(
                "SELECT source_kind AS kind,source_id AS id,source_revision AS revision,source_sha256 AS sha256 FROM platform_hr_agent.reference_edges WHERE owner_id=%s AND dependent_kind='result' AND dependent_id=%s AND dependent_revision=%s",
                (owner, current["id"], current["revision"]),
            )
            pending.extend(c.fetchall())

    def list_messages(self, owner_id, work_id, after=0, limit=100):
        if after < 0 or not 1 <= limit <= 200:
            raise problem("invalid_input")
        with self.transaction() as c:
            work = self._work(c, owner_id, work_id)
            current, _ = self._input(c, work)
            c.execute(
                "SELECT * FROM platform_hr_agent.entries WHERE owner_id=%s AND work_id=%s AND seq>%s AND kind IN ('user','assistant','question') ORDER BY seq LIMIT %s",
                (_uuid(owner_id), _uuid(work_id), after, limit + 1),
            )
            rows = c.fetchall()
            items = []
            for row in rows[:limit]:
                allowed = self._entry_allowed(c, work, current, row)
                body = (
                    self._unseal("entries", row["entry_id"], "sealed_body", row)
                    if allowed
                    else {}
                )
                items.append(
                    {
                        "entry_id": str(row["entry_id"]),
                        "seq": row["seq"],
                        "input_revision": row["input_revision"],
                        "kind": row["kind"],
                        "body": body.get("body") if allowed else None,
                        "options": body.get("options", []) if allowed else [],
                        "question_id": str(row["entry_id"])
                        if row["kind"] == "question"
                        else None,
                        "visibility": "available" if allowed else "restricted",
                    }
                )
            return {
                "items": items,
                "next_after": rows[min(limit, len(rows)) - 1]["seq"] if rows else after,
            }

    def _entry_allowed(self, c, work, current, row):
        if not _objects(row["objects"]) <= _objects(current["objects"]):
            return False
        try:
            self._scope(work["owner_id"], row["objects"], [], work["work_id"])
            for ref in row["source_refs"]:
                if ref["kind"] == "material" and ref not in current["references"]:
                    return False
                if ref["kind"] == "result":
                    self._validate_result_sources(
                        c, work["owner_id"], ref, work["work_id"]
                    )
                else:
                    self._scope(
                        work["owner_id"], row["objects"], [ref], work["work_id"]
                    )
            return True
        except HrAgentProblem:
            return False

    def list_events(self, owner_id, work_id, after=0, limit=100):
        if after < 0 or not 1 <= limit <= 200:
            raise problem("invalid_input")
        with self.transaction() as c:
            self._work(c, owner_id, work_id)
            c.execute(
                "SELECT * FROM platform_hr_agent.events WHERE owner_id=%s AND work_id=%s AND seq>%s ORDER BY seq LIMIT %s",
                (_uuid(owner_id), _uuid(work_id), after, limit + 1),
            )
            rows = c.fetchall()
            items = []
            for row in rows[:limit]:
                ref = row["result_ref"]
                if ref:
                    try:
                        self._validate_result_sources(
                            c, _uuid(owner_id), ref, _uuid(work_id)
                        )
                    except HrAgentProblem:
                        ref = None
                items.append(
                    {
                        "seq": row["seq"],
                        "type": row["type"],
                        "work_id": str(row["work_id"]),
                        "input_revision": row["input_revision"],
                        "at": row["created_at"].isoformat(),
                        "state": row["state"],
                        "phase": row["phase"],
                        "message": EVENT_MESSAGES[row["type"]],
                        "result_ref": ref,
                        "tool_status": row["tool_status"],
                        "error": problem(
                            row["error_code"], retryable=bool(row["error_retryable"])
                        ).problem
                        if row["error_code"]
                        else None,
                    }
                )
            return {
                "items": items,
                "next_after": rows[min(limit, len(rows)) - 1]["seq"] if rows else after,
            }

    def _attempt(self, c, work, attempt_id):
        c.execute(
            "SELECT * FROM platform_hr_agent.model_attempts WHERE owner_id=%s AND work_id=%s AND attempt_id=%s",
            (work["owner_id"], work["work_id"], _uuid(attempt_id)),
        )
        row = c.fetchone()
        if not row:
            raise problem("not_found", http_status=404)
        return row

    def _decode_request(self, row):
        payload = self._unseal(
            "model_attempts", row["attempt_id"], "sealed_request", row
        )
        request = payload["request"]
        request["attempt_id"] = _uuid(request["attempt_id"])
        request["messages"] = tuple(request["messages"])
        request["tools"] = tuple(request["tools"])
        return ModelRequest(**request), payload

    def prepare_model(self, fence, context):
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._input(c, work)
            self._charge_time(c, work)
            if context.input_revision != fence.input_revision:
                raise problem("lease_lost", http_status=409)
            self._scope(work["owner_id"], [], context.dependencies, work["work_id"])
            c.execute(
                "SELECT * FROM platform_hr_agent.model_attempts WHERE work_id=%s AND input_revision=%s ORDER BY ordinal DESC LIMIT 1",
                (work["work_id"], work["input_revision"]),
            )
            last = c.fetchone()
            if last and last["status"] == "prepared":
                return self._decode_request(last)[0]
            budget = self._unseal("works", work["work_id"], "sealed_budget", work)
            max_output = (
                self.settings.budget_profile["max_output_tokens"]
                if self.settings
                else 4096
            )
            reserved = context.estimated_input_tokens + max_output
            reserve = (
                budget["reserve"]
                if work["phase"] == "research"
                else dict.fromkeys(DEFAULT_RESERVE, 0)
            )
            sufficient = (
                budget["charged_calls"] + 1
                <= budget["limits"]["model_calls"] - reserve["model_calls"]
                and budget["charged_tokens"] + reserved
                <= budget["limits"]["total_tokens"] - reserve["total_tokens"]
                and budget["active_seconds"]
                < budget["limits"]["active_seconds"] - reserve["active_seconds"]
            )
            if not sufficient:
                if work["phase"] == "research":
                    self._update(
                        c, "works", "work_id", work["work_id"], {"phase": "finalizing"}
                    )
                    work["phase"] = "finalizing"
                    self._event(c, work, "state_changed")
                    raise ContextRebuildRequired()
                self._state(c, work, "waiting_budget")
                self._event(c, work, "state_changed")
                raise WorkPaused(self._view(c, work))
            retry = (
                last["retry_no"] + 1 if last and last["status"] == "interrupted" else 0
            )
            if retry > 2:
                self._state(c, work, "failed")
                self._event(c, work, "state_changed")
                raise WorkPaused(self._view(c, work))
            logical = last["logical_step_id"] if retry else uuid4()
            c.execute(
                "SELECT COALESCE(MAX(ordinal),0)+1 AS ordinal FROM platform_hr_agent.model_attempts WHERE work_id=%s",
                (work["work_id"],),
            )
            ordinal = c.fetchone()["ordinal"]
            identity = uuid4()
            profile = (
                self.settings.provider_profile["id"] if self.settings else "a1-test"
            )
            request = ModelRequest(
                identity,
                context.purpose,
                profile,
                context.messages,
                context.tools,
                max_output,
                min(120, budget["limits"]["active_seconds"] - budget["active_seconds"]),
            )
            payload = {
                "request": json.loads(canonical_json(asdict(request))),
                "dependencies": list(context.dependencies),
                "summary_provenance": context.summary_provenance,
                "estimated_input_tokens": context.estimated_input_tokens,
            }
            self._insert(
                c,
                "model_attempts",
                {
                    "owner_id": work["owner_id"],
                    "work_id": work["work_id"],
                    "input_revision": work["input_revision"],
                    "attempt_id": identity,
                    "ordinal": ordinal,
                    "purpose": context.purpose,
                    "logical_step_id": logical,
                    "retry_no": retry,
                    "status": "prepared",
                    "provider_profile": profile,
                    "reserved_tokens": reserved,
                    "charged_tokens": 0,
                    "usage_quality": "estimated",
                    **self._seal("model_attempts", identity, "sealed_request", payload),
                },
            )
            return request

    def mark_model_sending(self, fence, attempt_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._input(c, work)
            attempt = self._attempt(c, work, attempt_id)
            self._scope(
                work["owner_id"],
                [],
                self._decode_request(attempt)[1]["dependencies"],
                work["work_id"],
            )
            self._charge_time(c, work)
            if attempt["status"] == "sending":
                return
            if attempt["status"] != "prepared":
                raise problem("revision_conflict", http_status=409)
            budget = self._unseal("works", work["work_id"], "sealed_budget", work)
            reserve = (
                budget["reserve"]
                if work["phase"] == "research"
                else dict.fromkeys(DEFAULT_RESERVE, 0)
            )
            fits = (
                budget["charged_calls"] + 1
                <= budget["limits"]["model_calls"] - reserve["model_calls"]
                and budget["charged_tokens"] + attempt["reserved_tokens"]
                <= budget["limits"]["total_tokens"] - reserve["total_tokens"]
                and budget["active_seconds"]
                < budget["limits"]["active_seconds"] - reserve["active_seconds"]
            )
            if not fits:
                self._update(
                    c,
                    "model_attempts",
                    "attempt_id",
                    attempt["attempt_id"],
                    {"status": "superseded"},
                )
                if work["phase"] == "research":
                    self._update(
                        c, "works", "work_id", work["work_id"], {"phase": "finalizing"}
                    )
                    work["phase"] = "finalizing"
                    self._event(c, work, "state_changed")
                    raise ContextRebuildRequired()
                self._state(c, work, "waiting_budget")
                self._event(c, work, "state_changed")
                raise WorkPaused(self._view(c, work))
            budget["charged_calls"] += 1
            budget["charged_tokens"] += attempt["reserved_tokens"]
            budget["usage_quality"] = "mixed"
            self._save_budget(c, work, budget)
            self._update(
                c,
                "model_attempts",
                "attempt_id",
                attempt["attempt_id"],
                {"status": "sending", "charged_tokens": attempt["reserved_tokens"]},
            )

    def commit_model(self, fence, attempt_id, reply):
        if not isinstance(reply, ModelReply) or (
            not reply.text and not reply.tool_calls
        ):
            raise problem("invalid_input")
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._input(c, work)
            attempt = self._attempt(c, work, attempt_id)
            payload = json.loads(canonical_json(asdict(reply)))
            if attempt["status"] == "committed":
                if (
                    self._unseal(
                        "model_attempts", attempt["attempt_id"], "sealed_reply", attempt
                    )
                    != payload
                ):
                    raise problem("revision_conflict", http_status=409)
                c.execute(
                    "SELECT operation_id FROM platform_hr_agent.operations WHERE attempt_id=%s ORDER BY slot",
                    (attempt["attempt_id"],),
                )
                return tuple(row["operation_id"] for row in c.fetchall())
            if attempt["status"] != "sending":
                raise problem("lease_lost", http_status=409)
            if attempt["purpose"] == "summary" and reply.tool_calls:
                raise problem("invalid_input")
            call_ids = [call.provider_call_id for call in reply.tool_calls]
            if len(call_ids) != len(set(call_ids)) or any(
                not isinstance(call.arguments, dict) for call in reply.tool_calls
            ):
                raise problem("invalid_input")
            self._update(
                c,
                "model_attempts",
                "attempt_id",
                attempt["attempt_id"],
                {
                    "status": "committed",
                    "ended_at": datetime.now(UTC),
                    **self._seal(
                        "model_attempts", attempt["attempt_id"], "sealed_reply", payload
                    ),
                },
            )
            self._settle(c, work, attempt, uuid4(), reply.usage)
            operations = []
            for slot, call in enumerate(reply.tool_calls):
                identity = uuid4()
                operations.append(identity)
                self._insert(
                    c,
                    "operations",
                    {
                        "operation_id": identity,
                        "owner_id": work["owner_id"],
                        "work_id": work["work_id"],
                        "input_revision": work["input_revision"],
                        "attempt_id": attempt["attempt_id"],
                        "slot": slot,
                        "namespace": "tool:" + call.name,
                        "request_key": f"{attempt_id}:{slot}",
                        "request_hash": content_sha256(call.arguments),
                        "status": "prepared",
                        "lease_epoch": fence.epoch,
                        **self._seal(
                            "operations", identity, "sealed_arguments", call.arguments
                        ),
                    },
                )
            self._event(c, work, "model_committed")
            self._checkpoint(c, work)
            return tuple(operations)

    def _settle(self, c, work, attempt, observation_id, usage):
        if usage.input_total is None or usage.output_total is None:
            return
        if any(
            isinstance(n, bool) or not isinstance(n, int) or n < 0
            for n in [usage.input_total, usage.output_total]
        ):
            raise problem("invalid_input")
        fingerprint = content_sha256(
            {"input": usage.input_total, "output": usage.output_total}
        )
        if attempt["reported_usage_hash"]:
            if attempt["reported_usage_hash"] != fingerprint:
                raise problem("revision_conflict", http_status=409)
            return
        actual = usage.input_total + usage.output_total
        budget = self._unseal("works", work["work_id"], "sealed_budget", work)
        budget["charged_tokens"] += actual - attempt["charged_tokens"]
        budget["usage_quality"] = "mixed"
        self._save_budget(c, work, budget)
        self._update(
            c,
            "model_attempts",
            "attempt_id",
            attempt["attempt_id"],
            {
                "charged_tokens": actual,
                "usage_quality": "reported",
                "usage_observation_id": observation_id,
                "reported_usage_hash": fingerprint,
                **self._seal(
                    "model_attempts",
                    attempt["attempt_id"],
                    "raw_usage_cipher",
                    usage.raw or {},
                ),
            },
        )

    def settle_usage(self, worker, attempt_id, observation_id, usage):
        if (
            not isinstance(worker, WorkerIdentity)
            or not worker.worker_id
            or worker.profile_revision
            != getattr(self.settings, "configuration_revision", "a1-test")
        ):
            raise problem("scope_denied", http_status=403)
        with self.transaction() as c:
            c.execute(
                "SELECT owner_id,work_id FROM platform_hr_agent.model_attempts WHERE attempt_id=%s",
                (_uuid(attempt_id),),
            )
            row = c.fetchone()
            if not row:
                raise problem("not_found", http_status=404)
            work = self._work(c, row["owner_id"], row["work_id"], True)
            attempt = self._attempt(c, work, attempt_id)
            if attempt["status"] == "prepared" or (
                attempt["status"] == "superseded" and attempt["charged_tokens"] == 0
            ):
                raise problem("invalid_input")
            self._settle(c, work, attempt, _uuid(observation_id), usage)

    def interrupt_model(self, fence, attempt_id, reason):
        with self.transaction() as c:
            work = self._fence(c, fence)
            attempt = self._attempt(c, work, attempt_id)
            if attempt["status"] != "sending":
                raise problem("revision_conflict", http_status=409)
            self._update(
                c,
                "model_attempts",
                "attempt_id",
                attempt["attempt_id"],
                {"status": "interrupted", "ended_at": datetime.now(UTC)},
            )
            if (
                reason not in ("rate_limited", "transport_error", "incomplete_response")
                or attempt["retry_no"] >= 2
            ):
                self._state(c, work, "failed")
                self._event(c, work, "state_changed")

    def next_action(self, fence):
        with self.transaction() as c:
            work = self._fence(c, fence)
            c.execute(
                "SELECT * FROM platform_hr_agent.model_attempts WHERE work_id=%s AND input_revision=%s ORDER BY ordinal DESC LIMIT 1",
                (work["work_id"], work["input_revision"]),
            )
            row = c.fetchone()
            if not row:
                return RuntimeAction("build_context")
            if row["status"] == "prepared":
                return RuntimeAction("resume_prepared", row["attempt_id"])
            if row["status"] == "sending":
                self._update(
                    c,
                    "model_attempts",
                    "attempt_id",
                    row["attempt_id"],
                    {"status": "interrupted", "ended_at": datetime.now(UTC)},
                )
                return RuntimeAction("build_context")
            if row["status"] == "committed":
                c.execute(
                    "SELECT operation_id FROM platform_hr_agent.operations WHERE attempt_id=%s AND status='prepared' ORDER BY slot LIMIT 1",
                    (row["attempt_id"],),
                )
                op = c.fetchone()
                if op:
                    return RuntimeAction(
                        "execute_tool", row["attempt_id"], op["operation_id"]
                    )
                reply = self._unseal(
                    "model_attempts", row["attempt_id"], "sealed_reply", row
                )
                if not reply["tool_calls"]:
                    kind = "summary" if row["purpose"] == "summary" else "assistant"
                    c.execute(
                        "SELECT entry_id FROM platform_hr_agent.entries WHERE model_attempt_id=%s AND kind=%s",
                        (row["attempt_id"], kind),
                    )
                    if not c.fetchone():
                        return RuntimeAction(
                            "project_summary"
                            if kind == "summary"
                            else "project_answer",
                            row["attempt_id"],
                        )
            return RuntimeAction("build_context")

    def resume_prepared(self, fence, attempt_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._input(c, work)
            attempt = self._attempt(c, work, attempt_id)
            if attempt["status"] != "prepared":
                raise problem("revision_conflict", http_status=409)
            request, payload = self._decode_request(attempt)
            self._scope(work["owner_id"], [], payload["dependencies"], work["work_id"])
            self._charge_time(c, work)
            budget = self._unseal("works", work["work_id"], "sealed_budget", work)
            remaining = budget["limits"]["active_seconds"] - budget["active_seconds"]
            if (
                work["phase"] == "research"
                and remaining <= budget["reserve"]["active_seconds"]
            ):
                self._update(
                    c,
                    "model_attempts",
                    "attempt_id",
                    attempt["attempt_id"],
                    {"status": "superseded"},
                )
                self._update(
                    c, "works", "work_id", work["work_id"], {"phase": "finalizing"}
                )
                work["phase"] = "finalizing"
                self._event(c, work, "state_changed")
                raise ContextRebuildRequired()
            if remaining <= 0:
                self._state(c, work, "waiting_budget")
                self._event(c, work, "state_changed")
                raise WorkPaused(self._view(c, work))
            from dataclasses import replace

            return replace(request, deadline_seconds=min(120, remaining))

    def load_operation(self, fence, operation_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            row = self._operation(c, work, operation_id)
            return StoredToolOperation(
                row["operation_id"],
                row["namespace"].removeprefix("tool:"),
                self._unseal(
                    "operations", row["operation_id"], "sealed_arguments", row
                ),
                row["status"],
                self._unseal("operations", row["operation_id"], "sealed_receipt", row)
                if row["sealed_receipt"]
                else None,
            )

    def _operation(self, c, work, identity):
        c.execute(
            "SELECT * FROM platform_hr_agent.operations WHERE owner_id=%s AND work_id=%s AND input_revision=%s AND operation_id=%s FOR UPDATE",
            (
                work["owner_id"],
                work["work_id"],
                work["input_revision"],
                _uuid(identity),
            ),
        )
        row = c.fetchone()
        if not row or not row["namespace"].startswith("tool:"):
            raise problem("not_found", http_status=404)
        if row["status"] == "aborted":
            raise problem("cancelled", http_status=409)
        return row

    def _dependencies(self, c, work, operation):
        attempt = self._attempt(c, work, operation["attempt_id"])
        _, payload = self._decode_request(attempt)
        current, _ = self._input(c, work)
        dependencies = list(current["references"]) + payload["dependencies"]
        c.execute(
            "SELECT source_refs FROM platform_hr_agent.entries WHERE work_id=%s AND input_revision=%s AND kind='tool'",
            (work["work_id"], work["input_revision"]),
        )
        for row in c.fetchall():
            dependencies.extend(row["source_refs"])
        result = _refs(dependencies)
        for ref in result:
            if ref["kind"] == "result":
                self._validate_result_sources(c, work["owner_id"], ref, work["work_id"])
            else:
                self._scope(
                    work["owner_id"], current["objects"], [ref], work["work_id"]
                )
        return current, result

    def _tool_error(self, c, work, op, error):
        status = {
            "scope_denied": "forbidden",
            "not_found": "missing",
            "reference_unavailable": "missing",
            "temporarily_unavailable": "unavailable",
            "configuration_unavailable": "unavailable",
            "revision_conflict": "conflict",
            "hash_mismatch": "conflict",
        }.get(error.problem["code"], "invalid")
        outcome = {"status": status, "data": None, "error": error.problem}
        self._receipt(c, op["operation_id"], outcome)
        self._entry(
            c,
            work,
            "tool",
            {"operation_id": str(op["operation_id"]), "outcome": outcome},
            operation_id=op["operation_id"],
        )
        self._event(c, work, "tool_error", error=error.problem, status=status)
        self._checkpoint(c, work)
        return outcome

    def fail_tool(self, fence, operation_id, error):
        with self.transaction() as c:
            work = self._fence(c, fence)
            op = self._operation(c, work, operation_id)
            if op["status"] == "committed":
                return self._unseal(
                    "operations", op["operation_id"], "sealed_receipt", op
                )
            return self._tool_error(c, work, op, error)

    def execute_local_tool(self, fence, operation_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            op = self._operation(c, work, operation_id)
            current, dependencies = self._dependencies(c, work, op)
            if op["status"] == "committed":
                return self._unseal(
                    "operations", op["operation_id"], "sealed_receipt", op
                )
            name = op["namespace"].removeprefix("tool:")
            raw = self._unseal("operations", op["operation_id"], "sealed_arguments", op)
            try:
                args = validate_tool_arguments(name, raw)
                if name not in ("save_note", "save_result", "ask_user"):
                    raise problem("invalid_input")
                if "source_refs" in args and any(
                    r not in dependencies for r in args["source_refs"]
                ):
                    raise problem("scope_denied", http_status=403)
                if name == "save_note":
                    self._scope(
                        work["owner_id"],
                        current["objects"],
                        args["reading_targets"],
                        work["work_id"],
                    )
                    identity = self._entry(
                        c,
                        work,
                        "note",
                        args,
                        refs=dependencies,
                        objects=current["objects"],
                        operation_id=op["operation_id"],
                    )
                    data = {"entry_id": str(identity), "saved": True}
                elif name == "save_result":
                    declared = (
                        args["source_refs"]
                        + args["preceding_refs"]
                        + [b["ref"] for b in args["basis"] if b["ref"] is not None]
                    )
                    if args["base_standard_ref"] is not None:
                        declared.append(args["base_standard_ref"])
                    self._scope(
                        work["owner_id"], current["objects"], declared, work["work_id"]
                    )
                    dependencies = _refs((*dependencies, *declared))
                    data = self._save_result(c, work, op, args, current, dependencies)
                else:
                    identity = self._entry(
                        c,
                        work,
                        "question",
                        {"body": args["question"], "options": args["options"]},
                        refs=dependencies,
                        objects=current["objects"],
                        operation_id=op["operation_id"],
                    )
                    data = {"question_id": str(identity), "state": "waiting_user"}
                    self._state(c, work, "waiting_user")
                    c.execute(
                        "UPDATE platform_hr_agent.operations SET status='aborted' WHERE attempt_id=%s AND status='prepared' AND slot>%s",
                        (op["attempt_id"], op["slot"]),
                    )
                    self._event(c, work, "question_opened")
            except HrAgentProblem as error:
                return self._tool_error(c, work, op, error)
            outcome = {"status": "ok", "data": data, "error": None}
            self._receipt(c, op["operation_id"], outcome)
            self._entry(
                c,
                work,
                "tool",
                {"operation_id": str(op["operation_id"]), "outcome": outcome},
                refs=dependencies,
                objects=current["objects"],
                operation_id=op["operation_id"],
            )
            self._event(c, work, "tool_finished")
            self._checkpoint(c, work)
            return outcome

    def _save_result(self, c, work, op, args, current, dependencies):
        if not _objects(args["objects"]) <= _objects(current["objects"]):
            raise problem("scope_denied", http_status=403)
        self._scope(
            work["owner_id"],
            args["objects"],
            args["source_refs"] + args["preceding_refs"],
            work["work_id"],
        )
        for basis in args["basis"]:
            if basis["kind"] == "user_temporary":
                if basis["input_revision"] != work["input_revision"]:
                    raise problem("invalid_input")
            elif basis["kind"] == "confirmed_standard":
                self._scope(
                    work["owner_id"], args["objects"], [basis["ref"]], work["work_id"]
                )
            else:
                raise problem(
                    "unsupported_kind"
                )  # Official source verification is introduced at B1.
        proposal_document = None
        if args["kind"] == "standard_proposal":
            from .proposals import prepare_proposal

            proposal_document = prepare_proposal(
                self, c, work, op, args, current, dependencies
            )
        if args["result_id"]:
            identity = _uuid(args["result_id"])
            c.execute(
                "SELECT * FROM platform_hr_agent.results WHERE owner_id=%s AND result_id=%s FOR UPDATE",
                (work["owner_id"], identity),
            )
            previous = c.fetchone()
            if not previous:
                raise problem("not_found", http_status=404)
            if str(previous["current_revision"]) != args["expected_revision"]:
                raise problem(
                    "revision_conflict",
                    http_status=409,
                    details={
                        "conflict_kind": "result_revision",
                        "current_revision": str(previous["current_revision"]),
                    },
                )
            if previous["origin_work_id"] != work["work_id"]:
                raise problem("scope_denied", http_status=403)
            if previous["kind"] != args["kind"]:
                raise problem("invalid_input")
        else:
            identity = uuid4()
        revision = uuid4()
        document = (
            proposal_document
            if proposal_document is not None
            else {
                k: v
                for k, v in args.items()
                if k not in ("result_id", "expected_revision")
            }
        )
        if proposal_document is None:
            document["objects"] = list(_refs(current["objects"] + args["objects"]))
        digest = content_sha256(document)
        ref = {
            "kind": "result",
            "id": str(identity),
            "revision": str(revision),
            "sha256": digest,
        }
        if not args["result_id"]:
            self._insert(
                c,
                "results",
                {
                    "result_id": identity,
                    "owner_id": work["owner_id"],
                    "origin_work_id": work["work_id"],
                    "current_revision": revision,
                    "kind": args["kind"],
                },
            )
        self._insert(
            c,
            "result_revisions",
            {
                "revision_id": revision,
                "owner_id": work["owner_id"],
                "result_id": identity,
                "sha256": digest,
                "objects": Jsonb(document["objects"]),
                "created_by_operation": op["operation_id"],
                **self._seal("result_revisions", revision, "sealed_document", document),
            },
        )
        self._update(
            c, "results", "result_id", identity, {"current_revision": revision}
        )
        self._edges(
            c, work["owner_id"], "result", str(identity), str(revision), dependencies
        )
        self._event(c, work, "result_saved", ref=ref)
        return {**document, "ref": ref, "access_state": "available"}

    def commit_read(self, fence, operation_id, payload):
        with self.transaction() as c:
            work = self._fence(c, fence)
            op = self._operation(c, work, operation_id)
            current, dependencies = self._dependencies(c, work, op)
            if op["status"] == "committed":
                return self._unseal(
                    "operations", op["operation_id"], "sealed_receipt", op
                )
            if op["namespace"] == "tool:read_resource":
                payload = validate_contract("ResourceText", payload)
                args = self._unseal(
                    "operations", op["operation_id"], "sealed_arguments", op
                )
                if (
                    payload["ref"] != args["ref"]
                    or payload["end"] - payload["offset"] != len(payload["text"])
                    or not 0
                    <= payload["offset"]
                    <= payload["end"]
                    <= payload["total_characters"]
                ):
                    raise problem("invalid_input")
                self._scope(
                    work["owner_id"],
                    current["objects"],
                    [payload["ref"]],
                    work["work_id"],
                )
                identity = uuid4()
                payload["read_id"] = str(identity)
                self._insert(
                    c,
                    "read_records",
                    {
                        "read_id": identity,
                        "owner_id": work["owner_id"],
                        "work_id": work["work_id"],
                        "input_revision": work["input_revision"],
                        "operation_id": op["operation_id"],
                        "ref": Jsonb(payload["ref"]),
                        "start_offset": payload["offset"],
                        "end_offset": payload["end"],
                        "total_characters": payload["total_characters"],
                        "objects": Jsonb(current["objects"]),
                    },
                )
                dependencies = _refs((*dependencies, payload["ref"]))
            elif op["namespace"] == "tool:list_resources":
                payload = validate_contract("ResourcePage", payload)
                for item in payload["items"]:
                    self._scope(
                        work["owner_id"],
                        item["objects"],
                        [item["ref"]],
                        work["work_id"],
                    )
            else:
                raise problem("invalid_input")
            outcome = {
                "status": "empty"
                if "items" in payload and not payload["items"]
                else "ok",
                "data": payload,
                "error": None,
            }
            self._receipt(c, op["operation_id"], outcome)
            self._entry(
                c,
                work,
                "tool",
                {"operation_id": str(op["operation_id"]), "outcome": outcome},
                refs=dependencies,
                objects=current["objects"],
                operation_id=op["operation_id"],
            )
            self._event(c, work, "tool_finished")
            self._checkpoint(c, work)
            return outcome

    def pending_operations(self, fence):
        with self.transaction() as c:
            work = self._fence(c, fence)
            c.execute(
                "SELECT operation_id FROM platform_hr_agent.operations WHERE work_id=%s AND input_revision=%s AND status='prepared' AND attempt_id IS NOT NULL ORDER BY created_at,slot",
                (work["work_id"], work["input_revision"]),
            )
            return tuple(row["operation_id"] for row in c.fetchall())

    def _checkpoint(self, c, work):
        checkpoint = self._unseal("works", work["work_id"], "sealed_checkpoint", work)
        current, _ = self._input(c, work)
        targets = [r["ref"] for r in checkpoint["readings"]]
        c.execute(
            "SELECT * FROM platform_hr_agent.entries WHERE work_id=%s AND input_revision=%s AND kind='note' ORDER BY seq DESC LIMIT 1",
            (work["work_id"], work["input_revision"]),
        )
        note = c.fetchone()
        if note:
            body = self._unseal("entries", note["entry_id"], "sealed_body", note)
            checkpoint["open_questions"] = body["open_questions"]
            checkpoint["last_note_entry_id"] = str(note["entry_id"])
            targets.extend(body["reading_targets"])
        c.execute(
            "SELECT * FROM platform_hr_agent.read_records WHERE work_id=%s ORDER BY created_at",
            (work["work_id"],),
        )
        records = c.fetchall()
        targets.extend(r["ref"] for r in records)
        readings = []
        for ref in _refs(targets):
            try:
                self._scope(
                    work["owner_id"], current["objects"], [ref], work["work_id"]
                )
            except HrAgentProblem:
                continue
            relevant = [r for r in records if r["ref"] == ref]
            if not relevant:
                readings.append(
                    {
                        "ref": ref,
                        "total_characters": None,
                        "remaining_ranges": [],
                        "state": "unread",
                    }
                )
                continue
            total = relevant[0]["total_characters"]
            offset = 0
            remaining = []
            for start, end in sorted(
                (r["start_offset"], r["end_offset"]) for r in relevant
            ):
                if start > offset:
                    remaining.append({"start": offset, "end": start})
                offset = max(offset, end)
            if offset < total:
                remaining.append({"start": offset, "end": total})
            readings.append(
                {
                    "ref": ref,
                    "total_characters": total,
                    "remaining_ranges": remaining,
                    "state": "partial" if remaining else "returned",
                }
            )
        c.execute(
            "SELECT operation_id FROM platform_hr_agent.operations WHERE work_id=%s AND input_revision=%s AND status='prepared' AND attempt_id IS NOT NULL ORDER BY created_at,slot",
            (work["work_id"], work["input_revision"]),
        )
        checkpoint.update(
            revision=checkpoint["revision"] + 1,
            readings=readings,
            pending_operation_ids=[str(r["operation_id"]) for r in c.fetchall()],
        )
        changes = self._seal("works", work["work_id"], "sealed_checkpoint", checkpoint)
        work.update(changes)
        self._update(c, "works", "work_id", work["work_id"], changes)
        return checkpoint

    def update_checkpoint(self, fence):
        with self.transaction() as c:
            return self._checkpoint(c, self._fence(c, fence))

    def finish_work(self, fence, attempt_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            attempt = self._attempt(c, work, attempt_id)
            if attempt["status"] != "committed" or attempt["purpose"] != "work":
                raise problem("invalid_input")
            reply = self._unseal(
                "model_attempts", attempt["attempt_id"], "sealed_reply", attempt
            )
            if reply["tool_calls"]:
                raise problem("invalid_input")
            current, _ = self._input(c, work)
            _, request = self._decode_request(attempt)
            self._scope(
                work["owner_id"],
                current["objects"],
                request["dependencies"],
                work["work_id"],
            )
            self._entry(
                c,
                work,
                "assistant",
                {"body": reply["text"]},
                refs=request["dependencies"],
                objects=current["objects"],
                attempt_id=attempt["attempt_id"],
            )
            self._charge_time(c, work)
            self._state(
                c,
                work,
                "completed" if work["phase"] == "research" else "waiting_budget",
                answer_state="ended" if work["phase"] == "research" else "partial",
            )
            self._checkpoint(c, work)
            self._event(c, work, "state_changed")
            return self._view(c, work)

    def read_selected_entries(self, fence):
        with self.transaction() as c:
            work = self._fence(c, fence)
            current, _ = self._input(c, work)
            c.execute(
                "SELECT * FROM platform_hr_agent.entries WHERE owner_id=%s AND work_id=%s ORDER BY seq",
                (work["owner_id"], work["work_id"]),
            )
            rows = c.fetchall()
            selected = []
            covered = set()
            for row in reversed(rows):
                if row["entry_id"] in covered or not self._entry_allowed(
                    c, work, current, row
                ):
                    continue
                provenance = None
                if row["kind"] == "summary":
                    provenance = self._unseal(
                        "entries", row["entry_id"], "sealed_summary_provenance", row
                    )
                    sources = self._summary_coverage(
                        c, work, current, row, {r["entry_id"]: r for r in rows}
                    )
                    if sources is None:
                        continue
                    covered.update(sources)
                body = self._unseal("entries", row["entry_id"], "sealed_body", row)
                selected.append(
                    ScopedEntry(
                        row["entry_id"],
                        row["seq"],
                        row["kind"],
                        body,
                        tuple(row["objects"]),
                        tuple(row["source_refs"]),
                        row["input_revision"],
                        provenance,
                    )
                )
            return tuple(reversed(selected))

    def commit_summary(self, fence, attempt_id, provenance):
        provenance = validate_contract("SummaryProvenance", provenance)
        with self.transaction() as c:
            work = self._fence(c, fence)
            attempt = self._attempt(c, work, attempt_id)
            if attempt["purpose"] != "summary" or attempt["status"] != "committed":
                raise problem("invalid_input")
            _, request = self._decode_request(attempt)
            if request["summary_provenance"] != provenance:
                raise problem("scope_denied", http_status=403)
            c.execute(
                "SELECT entry_id FROM platform_hr_agent.entries WHERE model_attempt_id=%s AND kind='summary'",
                (attempt["attempt_id"],),
            )
            existing = c.fetchone()
            if existing:
                return existing["entry_id"]
            current, _ = self._input(c, work)
            objects = []
            refs = []
            for source in provenance["derived_from"]:
                c.execute(
                    "SELECT * FROM platform_hr_agent.entries WHERE owner_id=%s AND work_id=%s AND entry_id=%s AND seq=%s AND input_revision=%s",
                    (
                        work["owner_id"],
                        work["work_id"],
                        _uuid(source["entry_id"]),
                        source["seq"],
                        source["input_revision"],
                    ),
                )
                row = c.fetchone()
                if not row or not self._entry_allowed(c, work, current, row):
                    raise problem("scope_denied", http_status=403)
                objects.extend(row["objects"])
                refs.extend(row["source_refs"])
            reply = self._unseal(
                "model_attempts", attempt["attempt_id"], "sealed_reply", attempt
            )
            identity = self._entry(
                c,
                work,
                "summary",
                {"body": reply["text"]},
                refs=_refs(refs),
                objects=list(_refs(objects)),
                attempt_id=attempt["attempt_id"],
                provenance=provenance,
            )
            self._event(c, work, "context_compacted")
            return identity

    def worker_view(self, fence):
        # Trusted worker internal lookup; never exposed as an owner-free HTTP route.
        with self.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.works WHERE work_id=%s",
                (_uuid(fence.work_id),),
            )
            work = c.fetchone()
            if not work:
                raise problem("not_found", http_status=404)
            return self._view(c, work)

    def summary_provenance(self, fence, attempt_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            attempt = self._attempt(c, work, attempt_id)
            return self._decode_request(attempt)[1]["summary_provenance"]

    def pause_work(self, fence, code):
        if code not in (
            "configuration_unavailable",
            "dependency_revoked",
            "reference_unavailable",
            "scope_denied",
            "hash_mismatch",
        ):
            code = "configuration_unavailable"
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._state(c, work, "blocked")
            self._event(c, work, "state_changed", error=problem(code).problem)
            return self._view(c, work)

    def context_input(self, fence):
        with self.transaction() as c:
            work = self._fence(c, fence)
            current, record = self._input(c, work)
            if record["configuration_revision"] != getattr(
                self.settings, "configuration_revision", "a1-test"
            ):
                raise problem("configuration_unavailable", http_status=503)
            return current, record, self._view(c, work), work["owner_id"]

    def operation_context(self, fence, entry_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            c.execute(
                "SELECT operation_id FROM platform_hr_agent.entries WHERE owner_id=%s AND work_id=%s AND entry_id=%s",
                (work["owner_id"], work["work_id"], _uuid(entry_id)),
            )
            entry = c.fetchone()
            if not entry or not entry["operation_id"]:
                raise problem("not_found", http_status=404)
            c.execute(
                "SELECT * FROM platform_hr_agent.operations WHERE owner_id=%s AND work_id=%s AND operation_id=%s",
                (work["owner_id"], work["work_id"], entry["operation_id"]),
            )
            op = c.fetchone()
            if not op:
                raise problem("not_found", http_status=404)
            return {
                "id": str(op["operation_id"]),
                "name": op["namespace"].removeprefix("tool:"),
                "arguments": self._unseal(
                    "operations", op["operation_id"], "sealed_arguments", op
                ),
            }

    def wait_for_budget(self, fence, code="budget_exhausted"):
        with self.transaction() as c:
            work = self._fence(c, fence)
            self._state(c, work, "waiting_budget")
            self._event(c, work, "state_changed", error=problem(code).problem)
            return self._view(c, work)

    def validate_operation_dependencies(self, fence, operation_id):
        with self.transaction() as c:
            work = self._fence(c, fence)
            op = self._operation(c, work, operation_id)
            self._dependencies(c, work, op)
            args = self._unseal(
                "operations", op["operation_id"], "sealed_arguments", op
            )
            if op["namespace"] == "tool:read_resource":
                self._scope(work["owner_id"], [], [args["ref"]], work["work_id"])

    def _summary_coverage(self, c, work, current, root, index):
        pending = [root]
        seen = set()
        coverage = set()
        while pending:
            row = pending.pop()
            if row["entry_id"] in seen:
                continue
            seen.add(row["entry_id"])
            if len(seen) > 10000:
                return None
            if not self._entry_allowed(c, work, current, row):
                return None
            if row["kind"] != "summary":
                continue
            provenance = self._unseal(
                "entries", row["entry_id"], "sealed_summary_provenance", row
            )
            for source in provenance["derived_from"]:
                origin = index.get(_uuid(source["entry_id"]))
                if (
                    origin is None
                    or origin["seq"] != source["seq"]
                    or origin["input_revision"] != source["input_revision"]
                    or origin["seq"] >= row["seq"]
                ):
                    return None
                coverage.add(origin["entry_id"])
                pending.append(origin)
        return coverage
