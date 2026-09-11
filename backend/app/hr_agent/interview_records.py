"""Private registration and exact reads for user supplied interview transcripts."""

import re
from datetime import datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from .candidates import _source, _uuid
from .results import ResultService
from .types import problem, validate_contract


class InterviewRecordService:
    def __init__(self, repository, materials, candidates):
        self.repo, self.materials, self.candidates = repository, materials, candidates
        self.results = ResultService(repository)

    def _candidate(self, owner, candidate_id):
        self.candidates.read_candidate(owner, candidate_id)
        return _uuid(candidate_id)

    def _validate(self, request):
        required = {
            "material_ref",
            "title",
            "occurred_at",
            "position_id",
            "interview_plan_ref",
        }
        if not isinstance(request, dict) or set(request) != required:
            raise problem("invalid_input")
        ref = validate_contract("ExactRef", request["material_ref"])
        if ref["kind"] != "material" or not ref["id"].endswith(":text"):
            raise problem("invalid_input")
        title = request["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise problem("invalid_input")
        occurred = request["occurred_at"]
        if occurred is not None:
            if not isinstance(occurred, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
                occurred,
            ):
                raise problem("invalid_input")
            try:
                datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            except ValueError:
                raise problem("invalid_input") from None
        position = (
            None if request["position_id"] is None else _uuid(request["position_id"])
        )
        plan = request["interview_plan_ref"]
        if plan is not None:
            plan = validate_contract("ExactRef", plan)
            if plan["kind"] != "result":
                raise problem("invalid_input")
        return ref, title.strip(), occurred, position, plan

    def _authorize_relations(self, owner, candidate, position, plan):
        if position:
            with self.repo.transaction() as c:
                c.execute(
                    "SELECT 1 FROM platform_hr_agent.candidate_positions WHERE owner_id=%s AND candidate_id=%s AND position_id=%s",
                    (owner, candidate, position),
                )
                if not c.fetchone():
                    raise problem("scope_denied", http_status=403)
        if plan:
            view = self.results.read(owner, plan)
            if view["kind"] != "interview_plan":
                raise problem("invalid_input")
            objects = view["objects"]
            if {"kind": "candidate", "id": str(candidate)} not in objects:
                raise problem("scope_denied", http_status=403)
            if position and {"kind": "position", "id": str(position)} not in objects:
                raise problem("scope_denied", http_status=403)

    def _lock(self, c, owner, attachment, identity):
        c.execute(
            "SELECT platform_hr_agent.lock_user_input_source(%s,%s,%s) AS allowed",
            (owner, attachment, Jsonb(identity)),
        )
        if c.fetchone()["allowed"] is not True:
            raise problem("reference_unavailable", http_status=410)

    def register(self, owner, candidate_id, request, key):
        owner, candidate = _uuid(owner), self._candidate(owner, candidate_id)
        ref, title, occurred, position, plan = self._validate(request)
        text = self.materials.read_text(owner, ref)
        row = self.materials._row(owner, ref["id"].split(":", 1)[0])
        if (
            row["source_kind"] != "user_input"
            or row["detected_mime"] != "text/plain"
            or text.parser_release != "utf8-v1"
            or not text.text.strip()
            or len(text.text) > 32000
        ):
            raise problem("reference_unavailable", http_status=410)
        attachment, identity = row["attachment_id"], _source(row)
        self._authorize_relations(owner, candidate, position, plan)
        canonical = {
            **request,
            "title": title,
            "position_id": str(position) if position else None,
            "interview_plan_ref": plan,
        }
        with self.repo.transaction() as c:
            op, replay = self.repo._idempotency(
                c, owner, f"interview_record:{candidate}", key, canonical
            )
            if replay:
                self._lock(c, owner, attachment, identity)
                return replay
            self._lock(c, owner, attachment, identity)
            if position:
                c.execute(
                    "SELECT 1 FROM platform_hr_agent.candidate_positions WHERE owner_id=%s AND candidate_id=%s AND position_id=%s",
                    (owner, candidate, position),
                )
                if not c.fetchone():
                    raise problem("scope_denied", http_status=403)
            record = uuid4()
            sealed = self.repo._seal(
                "candidate_interview_records",
                record,
                "sealed_metadata",
                {"title": title, "occurred_at": occurred},
            )
            self.repo._insert(
                c,
                "candidate_interview_records",
                {
                    "record_id": record,
                    "owner_id": owner,
                    "candidate_id": candidate,
                    "attachment_id": attachment,
                    "material_ref": Jsonb(ref),
                    "source_identity": Jsonb(identity),
                    "position_id": position,
                    "interview_plan_ref": Jsonb(plan) if plan else None,
                    "created_by_operation": op,
                    **sealed,
                },
            )
            c.execute(
                "INSERT INTO platform_hr_agent.personal_materials(owner_id,attachment_id,source_identity,registered_by_record) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,attachment_id) DO NOTHING",
                (owner, attachment, Jsonb(identity), record),
            )
            c.execute(
                "SELECT source_identity FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
                (owner, attachment),
            )
            if c.fetchone()["source_identity"] != identity:
                raise problem("reference_unavailable", http_status=410)
            c.execute(
                "SELECT * FROM platform_hr_agent.candidate_interview_records WHERE record_id=%s",
                (record,),
            )
            return self.repo._receipt(
                c,
                op,
                self._view(
                    c.fetchone(), metadata={"title": title, "occurred_at": occurred}
                ),
            )

    def _view(self, row, metadata=None):
        metadata = metadata or self.repo._unseal(
            "candidate_interview_records", row["record_id"], "sealed_metadata", row
        )
        return {
            "record_id": str(row["record_id"]),
            "candidate_id": str(row["candidate_id"]),
            "material_ref": row["material_ref"],
            "title": metadata["title"],
            "occurred_at": metadata["occurred_at"],
            "position_id": str(row["position_id"]) if row["position_id"] else None,
            "interview_plan_ref": row["interview_plan_ref"],
            "authorship": "user_supplied",
            "created_at": row["created_at"].isoformat(),
        }

    def _authorized_row(self, owner, candidate_id, record_id=None):
        owner, candidate = _uuid(owner), self._candidate(owner, candidate_id)
        with self.repo.transaction() as c:
            sql = "SELECT * FROM platform_hr_agent.candidate_interview_records WHERE owner_id=%s AND candidate_id=%s"
            args = [owner, candidate]
            if record_id is not None:
                sql += " AND record_id=%s"
                args.append(_uuid(record_id))
            sql += " ORDER BY created_at,record_id"
            c.execute(sql, args)
            rows = c.fetchall()
        if record_id is not None and not rows:
            raise problem("not_found", http_status=404)
        for row in rows:
            self.materials.authorize_refs(owner, [row["material_ref"]])
            self._authorize_relations(
                owner, candidate, row["position_id"], row["interview_plan_ref"]
            )
        return owner, rows

    def list(self, owner, candidate_id):
        _, rows = self._authorized_row(owner, candidate_id)
        return {"items": [self._view(row) for row in rows]}

    def read(self, owner, candidate_id, record_id):
        owner, rows = self._authorized_row(owner, candidate_id, record_id)
        row = rows[0]
        view = self._view(row)
        text = self.materials.read_text(owner, row["material_ref"])
        with self.repo.transaction() as c:
            self._lock(c, owner, row["attachment_id"], row["source_identity"])
        return {**view, "text": text.text}
