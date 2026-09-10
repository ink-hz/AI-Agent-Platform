"""Immutable proposal preparation and conservative exact provenance checks."""

from copy import deepcopy
from uuid import UUID, uuid4

from .types import canonical_json, content_sha256, problem, validate_contract


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise problem("invalid_input") from None


def _personal(objects):
    if any(obj["kind"] == "candidate" for obj in objects):
        raise problem("personal_source_not_allowed")


def validate_sources(
    repository, cursor, owner, refs, work_id=None, *, reject_personal=True
):
    """Check every exact edge, including owner before decrypt, without current aliases."""
    pending = list(refs)
    seen = set()
    while pending:
        ref = validate_contract("ExactRef", pending.pop())
        identity = canonical_json(ref)
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > 10000:
            raise problem("temporarily_unavailable", http_status=503)
        if ref["kind"] == "result":
            cursor.execute(
                "SELECT * FROM platform_hr_agent.result_revisions WHERE owner_id=%s AND result_id=%s AND revision_id=%s",
                (_uuid(owner), _uuid(ref["id"]), _uuid(ref["revision"])),
            )
            row = cursor.fetchone()
            if not row:
                raise problem("not_found", http_status=404)
            if row["sha256"] != ref["sha256"]:
                raise problem("hash_mismatch", http_status=409)
            repository._scope(owner, row["objects"], [], work_id)
            if reject_personal:
                _personal(row["objects"])
            if row["sealed_document"] is None:
                raise problem("reference_unavailable", http_status=410)
        elif ref["kind"] == "standard":
            cursor.execute(
                "SELECT * FROM platform_hr_agent.standard_revisions WHERE owner_id=%s AND position_id=%s AND revision_id=%s",
                (_uuid(owner), _uuid(ref["id"]), _uuid(ref["revision"])),
            )
            row = cursor.fetchone()
            if not row:
                raise problem("not_found", http_status=404)
            repository._scope(
                owner, [{"kind": "position", "id": ref["id"]}], [], work_id
            )
            document = repository._unseal(
                "standard_revisions", row["revision_id"], "sealed_items", row
            )
            if content_sha256(document) != ref["sha256"]:
                raise problem("hash_mismatch", http_status=409)
        else:
            repository._scope(owner, [], [ref], work_id)
        if reject_personal and ref["kind"] == "material":
            # Persisted read and entry scopes mark known personal materials even if a
            # later proposal drops their candidate object or changes their title.
            attachment_id = ref["id"].split(":", 1)[0]
            cursor.execute(
                """SELECT 1 FROM platform_hr_agent.read_records
                WHERE owner_id=%s AND ref->>'kind'='material'
                AND split_part(ref->>'id',':',1)=%s
                AND objects @> '[{"kind":"candidate"}]'::jsonb
                UNION ALL SELECT 1 FROM platform_hr_agent.entries e
                WHERE owner_id=%s AND objects @> '[{"kind":"candidate"}]'::jsonb
                AND EXISTS (SELECT 1 FROM jsonb_array_elements(e.source_refs) r
                    WHERE r->>'kind'='material' AND split_part(r->>'id',':',1)=%s)
                LIMIT 1""",
                (_uuid(owner), attachment_id, _uuid(owner), attachment_id),
            )
            if cursor.fetchone():
                raise problem("personal_source_not_allowed")
        cursor.execute(
            "SELECT source_kind AS kind,source_id AS id,source_revision AS revision,source_sha256 AS sha256 FROM platform_hr_agent.reference_edges WHERE owner_id=%s AND dependent_kind=%s AND dependent_id=%s AND dependent_revision=%s",
            (_uuid(owner), ref["kind"], ref["id"], ref["revision"]),
        )
        pending.extend(cursor.fetchall())


def prepare_proposal(repository, cursor, work, operation, args, current, dependencies):
    args = validate_contract("SaveResultInput", args)
    if args["kind"] != "standard_proposal":
        raise problem("invalid_input")
    _personal(current["objects"])
    _personal(args["objects"])
    positions = [obj for obj in args["objects"] if obj["kind"] == "position"]
    if len(positions) != 1 or positions[0] not in current["objects"]:
        raise problem("invalid_input")
    repository._scope(work["owner_id"], args["objects"], [], work["work_id"])
    refs = (
        list(dependencies)
        + args["source_refs"]
        + args["preceding_refs"]
        + list(current.get("references", []))
    )
    refs.extend(b["ref"] for b in args["basis"] if b["ref"] is not None)
    base = args["base_standard_ref"]
    if base:
        refs.append(base)
    validate_sources(repository, cursor, work["owner_id"], refs, work["work_id"])
    from .standards import StandardService

    service = StandardService(repository)
    row = service._current_row(cursor, work["owner_id"], positions[0]["id"])
    if (row is None) != (base is None):
        raise problem("revision_conflict", http_status=409)
    items = []
    if base:
        if base["id"] != positions[0]["id"] or base["revision"] != str(
            row["revision_id"]
        ):
            raise problem("revision_conflict", http_status=409)
        if not any(
            b["kind"] == "confirmed_standard" and b["ref"] == base
            for b in args["basis"]
        ):
            raise problem("invalid_input")
        view = service._read_row(cursor, work["owner_id"], row)
        if view["ref"] != base:
            raise problem("hash_mismatch", http_status=409)
        items = view["items"]
    target_ids = {item["item_id"] for item in items}
    seen = set()
    for change in args["changes"]:
        target = change["target_item_id"]
        if change["action"] != "add":
            if target not in target_ids or target in seen:
                raise problem("invalid_input")
            seen.add(target)
    document = {
        k: deepcopy(v)
        for k, v in args.items()
        if k not in ("result_id", "expected_revision")
    }
    document["changes"] = [
        dict(change, change_id=str(uuid4())) for change in args["changes"]
    ]
    return document
