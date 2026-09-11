"""Private per-file intake over the existing cloud Loop and encrypted research results.

HTTP callers inject the authenticated owner; Worker callers still recheck HR authority.
No legacy candidate writes, model calls, implicit matching, or model confirmation tool.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from .material_parsing import source_identity
from .results import ResultService
from .types import HrAgentProblem, problem, validate_contract


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise problem("invalid_input") from None


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise problem("invalid_input")
    return value.strip()


def _version(value):
    if type(value) is not int or value < 1:
        raise problem("invalid_input")
    return value


def _source(row):
    return {**source_identity(row), "sha256": bytes(row["sha256"]).hex()}


def _gaps(total, ranges):
    remaining, end = [], 0
    for start, stop in sorted(ranges):
        if start > end:
            remaining.append([end, min(start, total)])
        end = max(end, stop)
    if end < total:
        remaining.append([end, total])
    return remaining


class CandidateIntakeService:
    def __init__(self, repository, materials, *, processing_authorizer=None):
        self.repo, self.materials = repository, materials
        self.processing_authorizer = processing_authorizer
        self.results = ResultService(repository)

    def _owner(self, owner, position=None):
        owner = _uuid(owner)
        objects = [{"kind": "position", "id": str(position)}] if position else []
        self.repo._scope(owner, objects, [])
        return owner

    def _row(self, owner, item_id, c=None, lock=False):
        if c is None:
            with self.repo.transaction() as cursor:
                return self._row(owner, item_id, cursor)
        c.execute(
            "SELECT * FROM platform_hr_agent.candidate_intake_items WHERE owner_id=%s AND item_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (owner, _uuid(item_id)),
        )
        row = c.fetchone()
        if not row:
            raise problem("not_found", http_status=404)
        return row

    def _batch(self, owner, batch_id, c=None):
        if c is None:
            with self.repo.transaction() as cursor:
                return self._batch(owner, batch_id, cursor)
        c.execute(
            "SELECT * FROM platform_hr_agent.candidate_batches WHERE owner_id=%s AND batch_id=%s",
            (owner, _uuid(batch_id)),
        )
        row = c.fetchone()
        if not row:
            raise problem("not_found", http_status=404)
        return row

    def _details(self, row):
        return self.repo._unseal(
            "candidate_intake_items", row["item_id"], "sealed_details", row
        )

    def _source_current(self, owner, row):
        current = self.materials._row(owner, row["attachment_id"])
        if current["state"] != "ready" or _source(current) != row["source_identity"]:
            raise problem("reference_unavailable", http_status=410)
        detail = self._details(row)
        if detail.get("text_ref"):
            self.materials.authorize_refs(owner, [detail["text_ref"]])
        return current

    def _lock_source(self, c, row):
        # Final source check shares the mutation transaction. No object I/O under lock.
        c.execute(
            "SELECT platform_hr_agent.lock_candidate_source(%s,%s,%s) AS allowed",
            (row["owner_id"], row["attachment_id"], Jsonb(row["source_identity"])),
        )
        if c.fetchone()["allowed"] is not True:
            raise problem("reference_unavailable", http_status=410)

    def create_batch(self, owner, request, key):
        if not isinstance(request, dict) or set(request) != {
            "attachment_ids",
            "position_id",
            "text",
            "budget_profile",
        }:
            raise problem("invalid_input")
        aids = request["attachment_ids"]
        if not isinstance(aids, list) or not 1 <= len(aids) <= 100:
            raise problem("invalid_input")
        aids = [_uuid(a) for a in aids]
        if len(set(aids)) != len(aids):
            raise problem("invalid_input")
        position = (
            _uuid(request["position_id"])
            if request["position_id"] is not None
            else None
        )
        owner = self._owner(owner, position)
        _text(request["text"], 32000)
        _text(request["budget_profile"], 200)
        self.repo._budget(request["budget_profile"])
        sources = [self.materials._row(owner, aid) for aid in aids]
        if any(r["state"] != "ready" for r in sources):
            raise problem("reference_unavailable", http_status=410)
        with self.repo.transaction() as c:
            op, replay = self.repo._idempotency(
                c, owner, "candidate_batch", key, request
            )
            if replay:
                for identity in replay["item_ids"]:
                    self._lock_source(c, self._row(owner, identity, c))
                return replay
            bid = uuid4()
            self.repo._insert(
                c,
                "candidate_batches",
                {
                    "owner_id": owner,
                    "batch_id": bid,
                    "position_id": position,
                    "created_by_operation": op,
                    **self.repo._seal(
                        "candidate_batches", bid, "sealed_request", request
                    ),
                },
            )
            ids = []
            for ordinal, row in enumerate(sources):
                identity = uuid5(bid, str(row["attachment_id"]))
                snapshot = _source(row)
                self._lock_source(
                    c,
                    {
                        "owner_id": owner,
                        "attachment_id": row["attachment_id"],
                        "source_identity": snapshot,
                    },
                )
                self.repo._insert(
                    c,
                    "candidate_intake_items",
                    {
                        "item_id": identity,
                        "owner_id": owner,
                        "batch_id": bid,
                        "attachment_id": row["attachment_id"],
                        "ordinal": ordinal,
                        "source_identity": Jsonb(snapshot),
                        **self.repo._seal(
                            "candidate_intake_items",
                            identity,
                            "sealed_details",
                            {
                                "parse_state": "not_started",
                                "text_ref": None,
                                "original_ref": None,
                                "coverage_complete": False,
                                "coverage_notes": [],
                                "unread_ranges": [],
                                "work_state": None,
                            },
                        ),
                    },
                )
                c.execute(
                    "INSERT INTO platform_hr_agent.personal_materials(owner_id,attachment_id,source_identity,registered_by_item) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,attachment_id) DO NOTHING",
                    (owner, row["attachment_id"], Jsonb(snapshot), identity),
                )
                c.execute(
                    "SELECT source_identity FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
                    (owner, row["attachment_id"]),
                )
                if c.fetchone()["source_identity"] != snapshot:
                    raise problem("reference_unavailable", http_status=410)
                ids.append(str(identity))
            return self.repo._receipt(c, op, {"batch_id": str(bid), "item_ids": ids})

    def list_batches(self, owner, limit=50):
        owner = self._owner(owner)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise problem("invalid_input")
        with self.repo.transaction() as c:
            c.execute(
                "SELECT batch_id,created_at FROM platform_hr_agent.candidate_batches WHERE owner_id=%s ORDER BY created_at DESC,batch_id DESC LIMIT %s",
                (owner, limit),
            )
            return {
                "items": [
                    {
                        "batch_id": str(r["batch_id"]),
                        "created_at": r["created_at"].isoformat(),
                    }
                    for r in c.fetchall()
                ]
            }

    def get_batch(self, owner, batch_id):
        owner = self._owner(owner)
        batch = self._batch(owner, batch_id)
        self._owner(owner, batch["position_id"])
        with self.repo.transaction() as c:
            c.execute(
                "SELECT item_id FROM platform_hr_agent.candidate_intake_items WHERE owner_id=%s AND batch_id=%s ORDER BY ordinal",
                (owner, batch["batch_id"]),
            )
            ids = [r["item_id"] for r in c.fetchall()]
        return {
            "batch_id": str(batch["batch_id"]),
            "position_id": str(batch["position_id"]) if batch["position_id"] else None,
            "items": [self.get_item(owner, i) for i in ids],
        }

    def get_item(self, owner, item_id):
        owner = self._owner(owner)
        row = self._row(owner, item_id)
        batch = self._batch(owner, row["batch_id"])
        self._owner(owner, batch["position_id"])
        detail = self._details(row)
        view = {
            "item_id": str(row["item_id"]),
            "batch_id": str(row["batch_id"]),
            "attachment_id": str(row["attachment_id"]),
            "state": row["state"],
            "row_version": row["row_version"],
            "generation": row["generation"],
            "work_id": str(row["work_id"]) if row["work_id"] else None,
            "result_ref": row["result_ref"],
            "error_code": row["error_code"],
            "failed_stage": row["failed_stage"],
            "profile_body": None,
            **detail,
        }
        try:
            self._source_current(owner, row)
            if row["result_ref"]:
                view["profile_body"] = self.results.read(owner, row["result_ref"])[
                    "body"
                ]
        except HrAgentProblem as error:
            if error.http_status not in (404, 410):
                raise
            view.update(
                state="failed",
                error_code="source_unavailable",
                profile_body=None,
                text_ref=None,
                original_ref=None,
                result_ref=None,
                coverage_notes=[],
                unread_ranges=[],
            )
        return view

    def _set(self, row, *, details=None, **changes):
        with self.repo.transaction() as c:
            current = self._row(row["owner_id"], row["item_id"], c, True)
            if current["row_version"] != row["row_version"]:
                return False
            if details is not None:
                changes.update(
                    self.repo._seal(
                        "candidate_intake_items",
                        row["item_id"],
                        "sealed_details",
                        details,
                    )
                )
            changes.update(
                row_version=row["row_version"] + 1, updated_at=datetime.now(UTC)
            )
            self.repo._update(
                c, "candidate_intake_items", "item_id", row["item_id"], changes
            )
            return True

    def advance_one(self, worker_id):
        _text(worker_id, 128)
        with self.repo.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.candidate_intake_items WHERE state IN ('queued','parsing','profiling') ORDER BY updated_at,item_id FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            row = c.fetchone()
            if not row:
                return False
            c.execute(
                "UPDATE platform_hr_agent.candidate_intake_items SET updated_at=now() WHERE item_id=%s",
                (row["item_id"],),
            )
        owner = row["owner_id"]
        try:
            batch = self._batch(owner, row["batch_id"])
            self._owner(owner, batch["position_id"])
            self._source_current(owner, row)
            try:
                allowed = (
                    self.processing_authorizer is not None
                    and self.processing_authorizer(owner, row["attachment_id"]) is True
                )
            except Exception:  # noqa: BLE001 - authority failures deny only this item
                allowed = False
            if not allowed:
                raise problem("processing_not_authorized")
            if row["state"] == "profiling":
                self._profile(row)
                return True
            detail = self._details(row)
            if not detail.get("text_ref"):
                view = self.materials.resolve(owner, row["attachment_id"])
                detail.update(
                    {
                        k: view.get(k)
                        for k in (
                            "parse_state",
                            "text_ref",
                            "original_ref",
                            "coverage_complete",
                            "coverage_notes",
                        )
                    }
                )
                detail["coverage_notes"] = detail["coverage_notes"] or []
                if view["parse_state"] == "unsupported":
                    self._set(
                        row,
                        details=detail,
                        state="failed",
                        failed_stage="parse",
                        error_code="unsupported_kind",
                    )
                    return True
                if view["parse_state"] == "failed":
                    self._set(
                        row,
                        details=detail,
                        state="failed",
                        failed_stage="parse",
                        error_code="parse_failed",
                    )
                    return True
                if not view["text_ref"]:
                    parser = getattr(self.materials, "parsing", None)
                    if parser is None:
                        raise problem("configuration_unavailable")
                    parser.request(
                        owner, row["attachment_id"], str(uuid5(row["item_id"], "parse"))
                    )
                    detail["parse_state"] = (
                        "queued"
                        if view["parse_state"] == "not_started"
                        else view["parse_state"]
                    )
                    self._set(row, details=detail, state="parsing")
                    return True
                text = self.materials.read_text(owner, view["text_ref"]).text
                detail["total_characters"] = len(text)
                detail["unread_ranges"] = [[0, len(text)]] if text else []
                if not text.strip():
                    self._set(
                        row,
                        details=detail,
                        state="failed",
                        failed_stage="parse",
                        error_code="empty_text",
                    )
                    return True
            request = self.repo._unseal(
                "candidate_batches", batch["batch_id"], "sealed_request", batch
            )
            body = {
                "thread_id": None,
                "text": request["text"],
                "objects": [{"kind": "position", "id": str(batch["position_id"])}]
                if batch["position_id"]
                else [],
                "references": [detail["text_ref"]],
                "budget_profile": request["budget_profile"],
            }
            work = self.repo.submit(
                owner, body, uuid5(row["item_id"], f"profile:{row['generation']}")
            )
            detail["work_state"] = work["state"]
            self._set(
                row, details=detail, state="profiling", work_id=_uuid(work["work_id"])
            )
        except HrAgentProblem as error:
            code = error.problem["code"]
            allowed = {"processing_not_authorized", "configuration_unavailable"}
            self._set(
                row,
                state="failed",
                failed_stage=(
                    "profile"
                    if row["state"] == "profiling" or code in allowed
                    else "parse"
                ),
                error_code=code if code in allowed else "source_unavailable",
            )
        return True

    def _profile(self, row):
        owner = row["owner_id"]
        detail = self._details(row)
        work = self.repo.get_work(owner, row["work_id"])
        if work["input_revision"] != 1:
            self._set(
                row,
                state="failed",
                failed_stage="profile",
                error_code="profile_scope_changed",
            )
            return
        detail["work_state"] = work["state"]
        with self.repo.transaction() as c:
            c.execute(
                "SELECT rr.result_id,rr.revision_id,rr.sha256 FROM platform_hr_agent.results r JOIN platform_hr_agent.result_revisions rr ON rr.owner_id=r.owner_id AND rr.result_id=r.result_id AND rr.revision_id=r.current_revision WHERE r.owner_id=%s AND r.origin_work_id=%s AND r.kind='research' ORDER BY rr.created_at DESC,rr.revision_id DESC LIMIT 1",
                (owner, row["work_id"]),
            )
            result = c.fetchone()
            c.execute(
                "SELECT start_offset,end_offset FROM platform_hr_agent.read_records WHERE owner_id=%s AND work_id=%s AND ref=%s ORDER BY start_offset",
                (owner, row["work_id"], Jsonb(detail["text_ref"])),
            )
            ranges = [(r["start_offset"], r["end_offset"]) for r in c.fetchall()]
        detail["unread_ranges"] = _gaps(detail["total_characters"], ranges)
        if result and any(end > start for start, end in ranges):
            ref = {
                "kind": "result",
                "id": str(result["result_id"]),
                "revision": str(result["revision_id"]),
                "sha256": result["sha256"],
            }
            view = self.results.read(owner, ref)
            if detail["text_ref"] in view["source_refs"]:
                self._set(
                    row, details=detail, state="awaiting_review", result_ref=Jsonb(ref)
                )
                return
        if work["state"] in ("completed", "failed", "cancelled", "blocked"):
            self._set(
                row,
                details=detail,
                state="failed",
                failed_stage="profile",
                error_code="profile_unread" if result else "profile_missing",
            )
        elif detail != self._details(row):
            self._set(row, details=detail)

    def retry_item(self, owner, item_id, request, key):
        owner = self._owner(owner)
        if (
            not isinstance(request, dict)
            or set(request) != {"expected_row_version", "stage"}
            or request["stage"] not in ("parse", "profile")
        ):
            raise problem("invalid_input")
        _version(request["expected_row_version"])
        row = self._row(owner, item_id)
        self._source_current(owner, row)
        self._owner(owner, self._batch(owner, row["batch_id"])["position_id"])
        with self.repo.transaction() as c:
            op, replay = self.repo._idempotency(
                c,
                owner,
                "candidate_retry",
                key,
                {"item_id": str(row["item_id"]), **request},
            )
            if replay:
                return replay
            row = self._row(owner, item_id, c, True)
            if (
                row["row_version"] != request["expected_row_version"]
                or row["state"] != "failed"
                or row["failed_stage"] != request["stage"]
            ):
                raise problem("revision_conflict", http_status=409)
            self._lock_source(c, row)
            detail = self._details(row)
            if request["stage"] == "parse":
                parser = getattr(self.materials, "parsing", None)
                if parser is None:
                    raise problem("configuration_unavailable")
                parser.retry(
                    owner,
                    row["attachment_id"],
                    uuid5(_uuid(key), "parse-retry"),
                    cursor=c,
                )
                detail.update(parse_state="queued", text_ref=None, work_state=None)
            else:
                detail["work_state"] = None
            self.repo._update(
                c,
                "candidate_intake_items",
                "item_id",
                row["item_id"],
                {
                    "state": "queued",
                    "error_code": None,
                    "failed_stage": None,
                    "generation": row["generation"] + 1,
                    "row_version": row["row_version"] + 1,
                    "work_id": None,
                    "result_ref": None,
                    "updated_at": datetime.now(UTC),
                    **self.repo._seal(
                        "candidate_intake_items",
                        row["item_id"],
                        "sealed_details",
                        detail,
                    ),
                },
            )
            return self.repo._receipt(
                c,
                op,
                {
                    "item_id": str(row["item_id"]),
                    "state": "queued",
                    "row_version": row["row_version"] + 1,
                },
            )

    def confirm_item(self, owner, item_id, request, key):
        owner = self._owner(owner)
        if not isinstance(request, dict) or set(request) != {
            "expected_row_version",
            "result_ref",
            "display_name",
            "summary",
            "decision",
            "reviewed_limitations",
        }:
            raise problem("invalid_input")
        _version(request["expected_row_version"])
        _text(request["display_name"], 500)
        _text(request["summary"], 16000)
        if type(request["reviewed_limitations"]) is not bool:
            raise problem("invalid_input")
        decision = request["decision"]
        if not isinstance(decision, dict) or not (
            (decision == {"kind": "create"})
            or (
                set(decision) == {"kind", "candidate_id"}
                and decision["kind"] == "link_existing"
            )
        ):
            raise problem("invalid_input")
        target = (
            _uuid(decision["candidate_id"])
            if decision["kind"] == "link_existing"
            else None
        )
        ref = validate_contract("ExactRef", request["result_ref"])
        row = self._row(owner, item_id)
        batch = self._batch(owner, row["batch_id"])
        self._owner(owner, batch["position_id"])
        self._source_current(owner, row)
        self.results.read(owner, ref)
        if target:
            self.read_candidate(owner, target)
        with self.repo.transaction() as c:
            op, replay = self.repo._idempotency(
                c,
                owner,
                "candidate_confirm",
                key,
                {"item_id": str(row["item_id"]), **request},
            )
            if replay:
                return replay
            row = self._row(owner, item_id, c, True)
            if (
                row["row_version"] != request["expected_row_version"]
                or row["state"] != "awaiting_review"
                or row["result_ref"] != ref
            ):
                raise problem("revision_conflict", http_status=409)
            self._lock_source(c, row)
            detail = self._details(row)
            if (
                not detail["coverage_complete"] or detail["unread_ranges"]
            ) and not request["reviewed_limitations"]:
                raise problem("review_required", http_status=409)
            candidate_id = target or uuid5(op, "candidate")
            if target:
                c.execute(
                    "SELECT 1 FROM platform_hr_agent.candidates WHERE owner_id=%s AND candidate_id=%s",
                    (owner, target),
                )
                if not c.fetchone():
                    raise problem("not_found", http_status=404)
            else:
                self.repo._insert(
                    c,
                    "candidates",
                    {
                        "candidate_id": candidate_id,
                        "owner_id": owner,
                        "created_by_operation": op,
                        **self.repo._seal(
                            "candidates",
                            candidate_id,
                            "sealed_profile",
                            {
                                "display_name": request["display_name"],
                                "summary": request["summary"],
                            },
                        ),
                    },
                )
            doc = uuid5(op, "document")
            self.repo._insert(
                c,
                "candidate_documents",
                {
                    "document_id": doc,
                    "owner_id": owner,
                    "candidate_id": candidate_id,
                    "item_id": row["item_id"],
                    "attachment_id": row["attachment_id"],
                    "source_ref": Jsonb(detail["text_ref"]),
                    "result_ref": Jsonb(ref),
                    "confirmed_by": owner,
                    "created_by_operation": op,
                    **self.repo._seal(
                        "candidate_documents",
                        doc,
                        "sealed_review",
                        {
                            "display_name": request["display_name"],
                            "summary": request["summary"],
                            "reviewed_limitations": request["reviewed_limitations"],
                            "coverage_notes": detail["coverage_notes"],
                            "unread_ranges": detail["unread_ranges"],
                        },
                    ),
                },
            )
            if batch["position_id"]:
                c.execute(
                    "INSERT INTO platform_hr_agent.candidate_positions(owner_id,candidate_id,position_id,created_by_operation) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (owner, candidate_id, batch["position_id"], op),
                )
            self.repo._update(
                c,
                "candidate_intake_items",
                "item_id",
                row["item_id"],
                {
                    "state": "confirmed",
                    "row_version": row["row_version"] + 1,
                    "updated_at": datetime.now(UTC),
                },
            )
            return self.repo._receipt(
                c,
                op,
                {
                    "candidate_id": str(candidate_id),
                    "document_id": str(doc),
                    "item_id": str(row["item_id"]),
                    "state": "confirmed",
                    "row_version": row["row_version"] + 1,
                },
            )

    def read_candidate(self, owner, candidate_id):
        owner = self._owner(owner)
        with self.repo.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.candidates WHERE owner_id=%s AND candidate_id=%s",
                (owner, _uuid(candidate_id)),
            )
            candidate = c.fetchone()
            if not candidate:
                raise problem("not_found", http_status=404)
            c.execute(
                "SELECT * FROM platform_hr_agent.candidate_documents WHERE owner_id=%s AND candidate_id=%s ORDER BY created_at,document_id",
                (owner, candidate["candidate_id"]),
            )
            documents = c.fetchall()
        self.materials.authorize_refs(owner, [d["source_ref"] for d in documents])
        for document in documents:
            self.results.read(owner, document["result_ref"])
        profile = self.repo._unseal(
            "candidates", candidate["candidate_id"], "sealed_profile", candidate
        )
        return {
            "candidate_id": str(candidate["candidate_id"]),
            **profile,
            "documents": [
                {
                    "document_id": str(d["document_id"]),
                    "attachment_id": str(d["attachment_id"]),
                    "source_ref": d["source_ref"],
                    "result_ref": d["result_ref"],
                    **self.repo._unseal(
                        "candidate_documents", d["document_id"], "sealed_review", d
                    ),
                }
                for d in documents
            ],
        }

    def list_candidates(self, owner, limit=50):
        owner = self._owner(owner)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise problem("invalid_input")
        with self.repo.transaction() as c:
            c.execute(
                "SELECT candidate_id FROM platform_hr_agent.candidates WHERE owner_id=%s ORDER BY created_at DESC,candidate_id DESC LIMIT %s",
                (owner, limit),
            )
            ids = [r["candidate_id"] for r in c.fetchall()]
        items = []
        for identity in ids:
            try:
                candidate = self.read_candidate(owner, identity)
                items.append(
                    {
                        "candidate_id": str(identity),
                        "display_name": candidate["display_name"],
                        "summary": candidate["summary"],
                        "available": True,
                    }
                )
            except HrAgentProblem as error:
                if error.http_status not in (404, 410):
                    raise
                items.append({"candidate_id": str(identity), "available": False})
        return {"items": items}
