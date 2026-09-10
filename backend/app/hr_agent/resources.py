"""Exact published text and owner-scoped material access, with no path tool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import sql

from .types import (
    HrAgentProblem,
    ResultQuery,
    canonical_json,
    problem,
    validate_contract,
)


class PublishedKnowledge:
    def __init__(self, directory):
        self.directory = Path(directory)
        raw = self._bytes("manifest.json")
        self.manifest_sha = hashlib.sha256(raw).hexdigest()
        try:
            value = json.loads(raw)
            if (
                set(value) != {"release_id", "role", "resources"}
                or not isinstance(value["release_id"], str)
                or value["release_id"].lower() in ("current", "latest")
                or not value["release_id"]
            ):
                raise ValueError()
            self.release_id = value["release_id"]
            self.role_spec = value["role"]
            self.items = []
            self.role = self._verified(self.role_spec).decode("utf8")
            seen = set()
            for item in value["resources"]:
                if set(item) != {"ref", "path", "title", "description", "objects"}:
                    raise ValueError()
                ref = validate_contract("ExactRef", item["ref"])
                if ref["kind"] not in ("method", "intelligence"):
                    raise ValueError()
                key = (ref["kind"], ref["id"], ref["revision"])
                if key in seen:
                    raise ValueError()
                seen.add(key)
                self._verified({"path": item["path"], "sha256": ref["sha256"]}).decode(
                    "utf8"
                )
                self.items.append(item)
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise problem("configuration_unavailable", http_status=503) from None

    def _bytes(self, name):
        try:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError()
            path = self.directory
            if path.is_symlink():
                raise ValueError()
            for component in relative.parts:
                path = path / component
                if path.is_symlink():
                    raise ValueError()
            if not path.is_file():
                raise ValueError()
            return path.read_bytes()
        except (OSError, TypeError, ValueError):
            raise problem("configuration_unavailable", http_status=503) from None

    def _verified(self, spec):
        raw = self._bytes(spec["path"])
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise problem("configuration_unavailable", http_status=503)
        return raw

    def check(self):
        if (
            hashlib.sha256(self._bytes("manifest.json")).hexdigest()
            != self.manifest_sha
        ):
            raise problem("configuration_unavailable", http_status=503)
        if self._verified(self.role_spec).decode("utf8") != self.role:
            raise problem("configuration_unavailable", http_status=503)

    def metadata(self):
        self.check()
        return {
            "role_release": self.release_id,
            "knowledge_release": self.release_id,
            "role_manifest_sha": self.manifest_sha,
        }

    def read(self, ref):
        self.check()
        for item in self.items:
            if item["ref"] == ref:
                return self._verified(
                    {"path": item["path"], "sha256": ref["sha256"]}
                ).decode("utf8")
        raise problem("reference_unavailable", http_status=410)


class ResourceReader:
    def __init__(
        self,
        repository,
        knowledge,
        *,
        material_service=None,
        authorize_objects=None,
        authorize_owner=None,
    ):
        self.repository = repository
        self.knowledge = knowledge
        self.materials = material_service
        self.authorize_objects = authorize_objects or self._database_objects
        self.authorize_owner = authorize_owner

    def _database_objects(self, owner, objects):
        with self.repository.connection_factory() as connection:
            for obj in objects:
                validate_contract("ObjectRef", obj)
                if obj["kind"] in ("company", "topic"):
                    if not any(obj in item["objects"] for item in self.knowledge.items):
                        raise problem("not_found", http_status=404)
                    continue
                try:
                    identity = UUID(obj["id"])
                except ValueError:
                    raise problem("not_found", http_status=404) from None
                table, column = (
                    ("positions", "position_id")
                    if obj["kind"] == "position"
                    else ("candidates", "candidate_id")
                )
                row = connection.execute(
                    sql.SQL(
                        "SELECT 1 FROM platform_hr.{} WHERE owner_internal_user_id=%s AND {}=%s"
                    ).format(sql.Identifier(table), sql.Identifier(column)),
                    (owner, identity),
                ).fetchone()
                if not row:
                    raise problem("not_found", http_status=404)

    def validate_scope(self, owner, objects, refs, work_id=None):
        if self.authorize_owner:
            self.authorize_owner(owner)
        self.authorize_objects(owner, objects)
        current = None
        if work_id:
            with self.repository.transaction() as c:
                work = self.repository._work(c, owner, work_id)
                c.execute(
                    "SELECT * FROM platform_hr_agent.inputs WHERE owner_id=%s AND work_id=%s AND revision=%s",
                    (owner, UUID(str(work_id)), work["input_revision"]),
                )
                row = c.fetchone()
                current = self.repository._unseal(
                    "inputs", row["input_id"], "sealed_input", row
                )
            if not {
                (o["kind"], o["id"])
                for o in objects
                if o["kind"] in ("position", "candidate")
            } <= {(o["kind"], o["id"]) for o in current["objects"]}:
                raise problem("scope_denied", http_status=403)
        for ref in refs:
            validate_contract("ExactRef", ref)
            if ref["kind"] in ("method", "intelligence"):
                self.knowledge.read(ref)
                item = next(i for i in self.knowledge.items if i["ref"] == ref)
                # Published public intelligence is discoverable without preselecting a company.
                if any(o["kind"] == "candidate" for o in item["objects"]):
                    raise problem("configuration_unavailable", http_status=503)
            elif ref["kind"] == "material":
                if current is not None and ref not in current["references"]:
                    raise problem("scope_denied", http_status=403)
                if self.materials is None:
                    raise problem("configuration_unavailable", http_status=503)
                self.materials.read_text(owner, ref)
            elif ref["kind"] == "result":
                with self.repository.transaction() as c:
                    self.repository._validate_result_sources(c, owner, ref, work_id)
            elif ref["kind"] == "standard":
                # Standard confirmation/storage integration is B3; don't borrow legacy semantics.
                raise problem("configuration_unavailable", http_status=503)
            else:
                raise problem("unsupported_kind")

    def for_work(self, fence):
        current, record, view, owner = self.repository.context_input(fence)
        self.knowledge.check()
        if record["role_manifest_sha"] not in (
            self.knowledge.manifest_sha,
            "0" * 64,
        ) or record["knowledge_release"] not in (self.knowledge.release_id, "a1-test"):
            raise problem("configuration_unavailable", http_status=503)
        return current, view, owner

    def list_resources(self, fence, args):
        args = validate_contract("ListResourcesInput", args)
        current, view, owner = self.for_work(fence)
        objects = args.get("objects", current["objects"])
        if not {(o["kind"], o["id"]) for o in objects} <= {
            (o["kind"], o["id"]) for o in current["objects"]
        }:
            raise problem("scope_denied", http_status=403)
        items = []
        for item in self.knowledge.items:
            if item["ref"]["kind"] in args["kinds"] and (
                not objects
                or not item["objects"]
                or any(o in item["objects"] for o in objects)
            ):
                self.knowledge.read(item["ref"])
                items.append(
                    {
                        "ref": item["ref"],
                        "title": item["title"],
                        "objects": item["objects"],
                        "description": item["description"],
                        "observed_at": None,
                        "state": "available",
                        "visibility": {"kind": "public", "subject_id": None},
                        "representation": "authored_text",
                        "original_ref": None,
                    }
                )
        if "material" in args["kinds"]:
            for ref in current["references"]:
                if ref["kind"] == "material":
                    self.validate_scope(owner, objects, [ref], fence.work_id)
                    text = self.materials.read_text(owner, ref)
                    items.append(
                        {
                            "ref": ref,
                            "title": "已选材料",
                            "objects": current["objects"],
                            "description": "用户为本次工作指定的材料正文",
                            "observed_at": None,
                            "state": "available",
                            "visibility": {"kind": "private", "subject_id": str(owner)},
                            "representation": "parsed_text",
                            "original_ref": text.original_ref,
                        }
                    )
        if "result" in args["kinds"]:
            cursor = None
            seen = set()
            while True:
                page = self.repository.list_results(
                    owner, ResultQuery(thread_id=UUID(view["thread_id"]), cursor=cursor)
                )
                for item in page["items"]:
                    try:
                        self.validate_scope(
                            owner, item["objects"], [item["ref"]], fence.work_id
                        )
                    except HrAgentProblem:
                        continue
                    items.append(item)
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                if cursor in seen:
                    raise problem("temporarily_unavailable", http_status=503)
                seen.add(cursor)
        query = args.get("query", "").casefold()
        if query:
            items = [
                i
                for i in items
                if query in (i["title"] + " " + i["description"]).casefold()
            ]
        items.sort(key=lambda i: canonical_json(i["ref"]))
        binding = {
            "owner": str(owner),
            "work": str(fence.work_id),
            "input": fence.input_revision,
            "args": {k: v for k, v in args.items() if k != "cursor"},
            "catalog": [i["ref"] for i in items],
        }
        start = 0
        if args.get("cursor"):
            from base64 import urlsafe_b64decode

            from app.execution_relay.content_crypto import SealedContent

            try:
                version, raw = args["cursor"].split(".", 1)
                data = self.repository.codec.unseal_json(
                    "hr-resource-page",
                    SealedContent(urlsafe_b64decode(raw), int(version)),
                )
                if (
                    data["binding"] != binding
                    or type(data["offset"]) is not int
                    or data["offset"] < 0
                ):
                    raise ValueError()
                start = data["offset"]
            except Exception:  # noqa: BLE001 - opaque authenticated cursor errors
                raise problem("invalid_input") from None
        from base64 import urlsafe_b64encode

        end = start + 30
        cursor = None
        if end < len(items):
            sealed = self.repository.codec.seal_json(
                "hr-resource-page", {"binding": binding, "offset": end}
            )
            cursor = (
                f"{sealed.key_version}." + urlsafe_b64encode(sealed.ciphertext).decode()
            )
        return validate_contract(
            "ResourcePage", {"items": items[start:end], "next_cursor": cursor}
        )

    def read_resource(self, fence, args):
        args = validate_contract("ReadResourceInput", args)
        current, _view, owner = self.for_work(fence)
        ref = args["ref"]
        self.validate_scope(owner, current["objects"], [ref], fence.work_id)
        if ref["kind"] in ("method", "intelligence"):
            text = self.knowledge.read(ref)
        elif ref["kind"] == "material":
            text = self.materials.read_text(owner, ref).text
        elif ref["kind"] == "result":
            text = self.repository.read_result(owner, ref["id"], ref["revision"])[
                "body"
            ]
        else:
            raise problem("unsupported_kind")
        start = args.get("offset", 0)
        limit = args.get("limit", 8000)
        if start > len(text):
            raise problem("invalid_input")
        end = min(len(text), start + limit)
        return {
            "ref": ref,
            "text": text[start:end],
            "offset": start,
            "end": end,
            "total_characters": len(text),
            "next_offset": end if end < len(text) else None,
            "read_id": str(uuid4()),
        }


def build_runtime_services(
    settings, connection_factory, *, material_service=None, agent_use_authorization=None
):
    from .repository import HrAgentRepository

    knowledge = PublishedKnowledge(settings.knowledge_dir)
    repository = HrAgentRepository(
        connection_factory, settings.create_codec(), settings=settings
    )

    def authorize_owner(owner):
        try:
            if agent_use_authorization is not None:
                allowed = agent_use_authorization.decide_for_user_id(
                    owner, "hr-bot"
                ).allowed
            else:
                with connection_factory() as connection:
                    row = connection.execute(
                        "SELECT allowed FROM platform_control.resolve_agent_use_decision_v41(%s,'hr-bot')",
                        (owner,),
                    ).fetchone()
                    allowed = row[0] if row else False
        except Exception:  # noqa: BLE001 - authorization backend errors must not disclose state
            raise problem(
                "temporarily_unavailable", retryable=True, http_status=503
            ) from None
        if allowed is not True:
            raise problem("scope_denied", http_status=403)

    resources = ResourceReader(
        repository,
        knowledge,
        material_service=material_service,
        authorize_owner=authorize_owner,
    )
    repository.scope_validator = resources.validate_scope
    repository.release_provider = knowledge.metadata
    return repository, resources
