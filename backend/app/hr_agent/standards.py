"""User-only atomic partial confirmation of immutable proposal revisions."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from .proposals import _uuid, validate_sources
from .types import content_sha256, problem, validate_contract


class StandardService:
    def __init__(self, repository):
        self.repository = repository

    def _current_row(self, c, owner, position):
        c.execute(
            "SELECT v.* FROM platform_hr_agent.standards s JOIN platform_hr_agent.standard_revisions v ON v.owner_id=s.owner_id AND v.position_id=s.position_id AND v.revision_id=s.current_revision WHERE s.owner_id=%s AND s.position_id=%s",
            (_uuid(owner), _uuid(position)),
        )
        return c.fetchone()

    def _read_row(self, c, owner, row):
        repo = self.repository
        repo._scope(owner, [{"kind": "position", "id": str(row["position_id"])}], [])
        # Validate source permissions before returning any private standard content.
        c.execute(
            "SELECT source_kind AS kind,source_id AS id,source_revision AS revision,source_sha256 AS sha256 FROM platform_hr_agent.reference_edges WHERE owner_id=%s AND dependent_kind='standard' AND dependent_id=%s AND dependent_revision=%s",
            (_uuid(owner), str(row["position_id"]), str(row["revision_id"])),
        )
        validate_sources(repo, c, owner, c.fetchall())
        document = repo._unseal(
            "standard_revisions", row["revision_id"], "sealed_items", row
        )
        ref = {
            "kind": "standard",
            "id": str(row["position_id"]),
            "revision": str(row["revision_id"]),
            "sha256": content_sha256(document),
        }
        view = {k: v for k, v in document.items() if k != "change_item_ids"}
        return validate_contract("StandardView", dict(view, ref=ref))

    def current(self, owner_id, position_id):
        owner, position = _uuid(owner_id), _uuid(position_id)
        with self.repository.transaction() as c:
            self.repository._scope(
                owner, [{"kind": "position", "id": str(position)}], []
            )
            row = self._current_row(c, owner, position)
            if row is None:
                raise problem("not_found", http_status=404)
            return self._read_row(c, owner, row)

    def read(self, owner_id, ref):
        ref = validate_contract("ExactRef", ref)
        if ref["kind"] != "standard":
            raise problem("invalid_input")
        owner = _uuid(owner_id)
        with self.repository.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.standard_revisions WHERE owner_id=%s AND position_id=%s AND revision_id=%s",
                (owner, _uuid(ref["id"]), _uuid(ref["revision"])),
            )
            row = c.fetchone()
            if row is None:
                raise problem("not_found", http_status=404)
            view = self._read_row(c, owner, row)
            if view["ref"] != ref:
                raise problem("hash_mismatch", http_status=409)
            return view

    @staticmethod
    def _conflict(kind, revision):
        error = problem(
            "revision_conflict",
            http_status=409,
            details={
                "conflict_kind": kind,
                "current_revision": str(revision) if revision else None,
            },
        )
        validate_contract("ConfirmError", error.problem)
        raise error

    def confirm(self, owner_id, position_id, request, key):
        request = validate_contract("ConfirmInput", request)
        owner, position, key = _uuid(owner_id), _uuid(position_id), _uuid(key)
        ref = request["proposal_ref"]
        if ref["kind"] != "result":
            raise problem("invalid_input")
        repo = self.repository
        with repo.transaction() as c:
            repo._scope(owner, [{"kind": "position", "id": str(position)}], [])
            # This slot exists even before the first standard row. Lock order is
            # slot -> proposal -> idempotency, shared by every confirmation.
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"hr-standard:{owner}:{position}",),
            )
            c.execute(
                "SELECT * FROM platform_hr_agent.results WHERE owner_id=%s AND result_id=%s FOR UPDATE",
                (owner, _uuid(ref["id"])),
            )
            proposal = c.fetchone()
            if proposal is None:
                raise problem("not_found", http_status=404)
            validate_sources(repo, c, owner, [ref])
            c.execute(
                "SELECT * FROM platform_hr_agent.result_revisions WHERE owner_id=%s AND result_id=%s AND revision_id=%s",
                (owner, _uuid(ref["id"]), _uuid(ref["revision"])),
            )
            row = c.fetchone()
            if row is None:
                raise problem("not_found", http_status=404)
            document = repo._unseal(
                "result_revisions", row["revision_id"], "sealed_document", row
            )
            if content_sha256(document) != ref["sha256"]:
                raise problem("hash_mismatch", http_status=409)
            if (
                proposal["kind"] != "standard_proposal"
                or document["kind"] != "standard_proposal"
            ):
                raise problem("invalid_input")
            targets = [o for o in document["objects"] if o["kind"] == "position"]
            if targets != [{"kind": "position", "id": str(position)}]:
                raise problem("invalid_input")
            refs = (
                document["source_refs"]
                + document["preceding_refs"]
                + [b["ref"] for b in document["basis"] if b["ref"] is not None]
            )
            if document["base_standard_ref"]:
                refs.append(document["base_standard_ref"])
            validate_sources(repo, c, owner, refs)
            op, replayed = repo._idempotency(
                c, owner, f"standard:confirm:{position}", key, request
            )
            if replayed is not None:
                # Old proposal/base revisions are allowed only for an identical,
                # already committed request, after revalidating its full ancestry.
                self.read(owner, replayed["ref"])
                return replayed
            current = self._current_row(c, owner, position)
            view = self._read_row(c, owner, current) if current else None
            revision = str(current["revision_id"]) if current else None
            if str(proposal["current_revision"]) != ref["revision"]:
                self._conflict("proposal_revision", revision)
            base = document["base_standard_ref"]
            if (
                request["expected_standard_revision"] != revision
                or (base["revision"] if base else None) != revision
                or (base is not None and base["id"] != str(position))
            ):
                self._conflict("standard_revision", revision)
            items = []
            if current:
                if view["ref"] != base:
                    self._conflict("standard_revision", revision)
                items = deepcopy(view["items"])
            changes = {
                change["change_id"]: validate_contract("Change", change)
                for change in document["changes"]
            }
            if len(changes) != len(document["changes"]):
                raise problem("invalid_input")
            selected = request["selected_change_ids"]
            if any(identity not in changes for identity in selected):
                raise problem("invalid_input")
            targets = set()
            mapping = {}
            by_id = {item["item_id"]: item for item in items}
            for identity in selected:
                change = changes[identity]
                target = change["target_item_id"]
                if change["action"] == "add":
                    item_id = str(uuid4())
                    items.append({"item_id": item_id, "text": change["text"]})
                    mapping[identity] = item_id
                else:
                    if target in targets or target not in by_id:
                        raise problem("invalid_input")
                    targets.add(target)
                    mapping[identity] = target
                    if change["action"] == "replace":
                        by_id[target]["text"] = change["text"]
                    else:
                        items = [item for item in items if item["item_id"] != target]
            identity = uuid4()
            body = {
                "position_id": str(position),
                "items": items,
                "confirmed_by": str(owner),
                "confirmed_at": datetime.now(UTC).isoformat(),
                "proposal_ref": ref,
                "selected_change_ids": selected,
                "change_item_ids": mapping,
            }
            new_ref = {
                "kind": "standard",
                "id": str(position),
                "revision": str(identity),
                "sha256": content_sha256(body),
            }
            repo._insert(
                c,
                "standard_revisions",
                dict(
                    owner_id=owner,
                    revision_id=identity,
                    position_id=position,
                    previous_revision=_uuid(revision) if revision else None,
                    proposal_ref=Jsonb(ref),
                    selected_change_ids=Jsonb(selected),
                    confirmed_by=owner,
                    created_by_operation=op,
                    **repo._seal("standard_revisions", identity, "sealed_items", body),
                ),
            )
            if current:
                c.execute(
                    "UPDATE platform_hr_agent.standards SET current_revision=%s WHERE owner_id=%s AND position_id=%s",
                    (identity, owner, position),
                )
            else:
                repo._insert(
                    c,
                    "standards",
                    {
                        "owner_id": owner,
                        "position_id": position,
                        "current_revision": identity,
                    },
                )
            repo._edges(
                c,
                owner,
                "standard",
                str(position),
                str(identity),
                [ref] + ([base] if base else []),
            )
            view = validate_contract(
                "StandardView",
                {
                    **{k: v for k, v in body.items() if k != "change_item_ids"},
                    "ref": new_ref,
                },
            )
            return repo._receipt(c, op, view)
