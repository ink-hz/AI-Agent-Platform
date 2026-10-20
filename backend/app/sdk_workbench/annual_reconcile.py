"""Idempotent reconciliation of the 2026 source plan into existing ZenTao stories.

Read CSV bytes on stdin. Dry run is the default; --apply writes only verified matches.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .plan_csv import parse_plan_csv
from .plan_mapping import MappedRequirement, map_plan, match_responsible
from .provision import ProvisionConflict, ZenTaoProvisioner
from .sso_repository import SdkSsoRepository, _ZEN_PROVISION_LOCK
from .zentao import ZenTaoClient


_MARKER = re.compile(r"SDK2026PLAN-([0-9a-f]{8})-R(\d{4})")


def _imported_title(value: str) -> str:
    """The original ZenTao import flattened CSV line breaks to spaces."""
    return " ".join(value.split())


def _source_rows(connection, sha256: str) -> dict[int, dict]:
    rows = connection.execute(
        "select row.id,row.record_number,row.physical_line,row.kind,row.module,row.cells "
        "from sdk.plan_rows row join sdk.plan_imports batch on batch.id=row.import_id "
        "where batch.sha256=%s order by row.record_number", (sha256,),
    ).fetchall()
    return {row["record_number"]: row for row in rows}


def _scoped_people(connection) -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute("select * from sdk.scoped_people()").fetchall():
        by_name[row["display_name"]].append(row)
    return by_name


async def _stories(client: ZenTaoClient, product_id: int, sha256: str) -> dict[int, dict]:
    first = await client.list_stories(product_id, 1)
    pager = first.get("pager")
    page_total = pager.get("pageTotal") if isinstance(pager, dict) else None
    if not isinstance(page_total, int) or not 1 <= page_total <= 100:
        raise RuntimeError("sdk_story_pager_invalid")
    summaries = list(first["stories"])
    for page in range(2, page_total + 1):
        summaries.extend((await client.list_stories(product_id, page))["stories"])
    found: dict[int, dict] = {}
    for summary in summaries:
        story = await client.get_story(int(summary["id"]))
        spec = story.get("spec", "")
        marker = _MARKER.search(spec) if isinstance(spec, str) else None
        if not marker:
            continue
        record_number = int(marker.group(2))
        if marker.group(1) != sha256[:8] or sha256 not in spec or record_number in found:
            raise RuntimeError("sdk_story_marker_conflict")
        found[record_number] = story
    return found


def _prepare_account(connection, provisioner: ZenTaoProvisioner, person: dict) -> str:
    member_key = person["member_key"]
    connection.execute("select pg_advisory_lock(%s)", (_ZEN_PROVISION_LOCK,))
    connection.commit()
    try:
        current = connection.execute(
            "select * from sdk.scoped_people() where member_key=%s", (member_key,),
        ).fetchone()
        if not current or current["display_name"] != person["display_name"] or not current["identity_available"]:
            raise RuntimeError("sdk_directory_person_changed")
        if current["bound_account"]:
            return current["bound_account"]
        if current["intent_account"]:
            raise RuntimeError("sdk_directory_provision_in_progress")
        account = SdkSsoRepository.account_for(member_key)
        row = connection.execute(
            "select account,marker,state from sdk.directory_accounts where member_key=%s", (member_key,),
        ).fetchone()
        if row is None:
            marker = "sdk:" + secrets.token_hex(16)
            connection.execute(
                "insert into sdk.directory_accounts(member_key,account,marker,display_name,state) "
                "values (%s,%s,%s,%s,'reserved')",
                (member_key, account, marker, person["display_name"]),
            )
            connection.commit()
            row = {"account": account, "marker": marker, "state": "reserved"}
        if row["account"] != account:
            raise RuntimeError("sdk_directory_account_conflict")
        if row["state"] == "reserved":
            try:
                provisioner.create(account, person["display_name"], row["marker"])
            except ProvisionConflict:
                provisioner.reconcile(account, person["display_name"], row["marker"])
            connection.execute(
                "update sdk.directory_accounts set state='ready',updated_at=clock_timestamp() "
                "where member_key=%s and account=%s and marker=%s and state='reserved'",
                (member_key, account, row["marker"]),
            )
            connection.commit()
        else:
            provisioner.reconcile(account, person["display_name"], row["marker"])
        return account
    finally:
        connection.rollback()
        connection.execute("select pg_advisory_unlock(%s)", (_ZEN_PROVISION_LOCK,))
        connection.commit()


def _save_source(connection, source_row: dict, item: MappedRequirement, story_id: int,
                 state: str, account: str | None) -> None:
    existing = connection.execute(
        "select story_id from sdk.plan_story_sources where plan_row_id=%s", (source_row["id"],),
    ).fetchone()
    if existing and existing["story_id"] != story_id:
        raise RuntimeError("sdk_source_story_conflict")
    connection.execute(
        "insert into sdk.plan_story_sources(plan_row_id,story_id,proposer_name,responsible_raw,assignee_account,"
        "assignment_state,planned_start,planned_end,source_priority,source_version,source_risk,source_status,source_note) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "on conflict (plan_row_id) do update set responsible_raw=excluded.responsible_raw,"
        "assignee_account=excluded.assignee_account,assignment_state=excluded.assignment_state,"
        "planned_start=excluded.planned_start,planned_end=excluded.planned_end,"
        "source_priority=excluded.source_priority,source_version=excluded.source_version,"
        "source_risk=excluded.source_risk,source_status=excluded.source_status,"
        "source_note=excluded.source_note,updated_at=clock_timestamp()",
        (source_row["id"], story_id, item.proposer, item.responsible_raw, account,
         state, item.planned_start, item.planned_end, item.priority, item.version,
         item.risk, item.source_status, item.note),
    )
    connection.commit()


async def reconcile(payload: bytes, *, expected_sha: str, apply: bool, limit: int | None = None) -> dict:
    parsed = parse_plan_csv(payload)
    if parsed.sha256 != expected_sha:
        raise RuntimeError("sdk_plan_digest_mismatch")
    mapped = map_plan(parsed)
    dsn = Path(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"]).read_text().strip()
    product_id = int(os.environ["PLATFORM_SDK_ZENTAO_PRODUCT_ID"])
    client = ZenTaoClient(
        os.environ["PLATFORM_SDK_ZENTAO_BASE_URL"],
        os.environ["PLATFORM_SDK_ZENTAO_ACCOUNT"],
        os.environ["PLATFORM_SDK_ZENTAO_PASSWORD_FILE"],
    )
    provisioner = ZenTaoProvisioner(
        os.environ["PLATFORM_SDK_ZENTAO_BASE_URL"],
        os.environ["PLATFORM_SDK_PROVISION_ACCOUNT"],
        os.environ["PLATFORM_SDK_PROVISION_PASSWORD_FILE"],
        product_id=product_id, group_id=int(os.environ["PLATFORM_SDK_PROVISION_GROUP_ID"]),
    )
    try:
        with psycopg.connect(dsn, row_factory=dict_row, options="-c statement_timeout=10000") as connection:
            source_rows = _source_rows(connection, parsed.sha256)
            if len(source_rows) != len(parsed.rows):
                raise RuntimeError("sdk_plan_source_count_mismatch")
            for row in parsed.rows:
                saved = source_rows[row.record_number]
                if (saved["kind"], saved["physical_line"], saved["module"], saved["cells"]) != (
                    row.kind, row.physical_line, row.module, list(row.cells)
                ):
                    raise RuntimeError("sdk_plan_source_drift")
            people = _scoped_people(connection)
            stories = await _stories(client, product_id, parsed.sha256)
            if set(stories) != {item.record_number for item in mapped}:
                raise RuntimeError("sdk_story_coverage_mismatch")
            for item in mapped:
                story = stories[item.record_number]
                if story["title"] != _imported_title(item.title) or int(story["product"]) != product_id or story.get("deleted") in (1, "1", True):
                    raise RuntimeError(f"sdk_story_identity_drift:{item.record_number}")
                if item.priority is not None and int(story["pri"]) != item.priority:
                    raise RuntimeError(f"sdk_story_priority_drift:{item.record_number}")
            report: Counter[str] = Counter()
            details: list[dict] = []
            candidate_keys = {name: [str(person["member_key"]) for person in matches]
                              for name, matches in people.items()}
            for index, item in enumerate(mapped):
                if limit is not None and index >= limit:
                    break
                story = stories[item.record_number]
                story_id = int(story["id"])
                state, member_key = match_responsible(item.responsible_raw, candidate_keys)
                person = next((p for p in people[item.responsible_raw] if str(p["member_key"]) == member_key), None) if member_key else None
                current = story.get("assignedTo") or None
                if state == "matched" and not person["identity_available"]:
                    state = "inactive_identity"
                if state == "matched" and person["intent_account"] and not person["bound_account"]:
                    state = "pending"
                if state == "matched" and current and current != (person["bound_account"] or SdkSsoRepository.account_for(person["member_key"])):
                    state = "conflict"
                if apply:
                    if state == "matched":
                        account = _prepare_account(connection, provisioner, person)
                        await client.assign_story(story_id, account, expected_version=int(story["version"]))
                    native_assignee = (await client.get_story(story_id)).get("assignedTo") or None
                    if state == "matched" and native_assignee != account:
                        raise RuntimeError("sdk_assignment_readback_conflict")
                    _save_source(connection, source_rows[item.record_number], item, story_id, state, native_assignee)
                report[state] += 1
                details.append({
                    "record_number": item.record_number, "physical_line": item.physical_line,
                    "story_id": story_id, "title": item.title,
                    "proposer": item.proposer, "responsible_raw": item.responsible_raw,
                    "assignment_state": state, "member_key": member_key,
                    "candidate_account": ((person["bound_account"] or SdkSsoRepository.account_for(person["member_key"]))
                                          if person and state == "matched" else None),
                    "current_assignee": current,
                    "planned_start": item.planned_start.isoformat() if item.planned_start else None,
                    "planned_end": item.planned_end.isoformat() if item.planned_end else None,
                    "source_priority": item.priority,
                })
            return {"sha256": parsed.sha256, "source_tasks": len(mapped), "processed": sum(report.values()),
                    "states": dict(report), "applied": apply, "rows": details}
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    import json
    result = asyncio.run(reconcile(sys.stdin.buffer.read(), expected_sha=args.sha256,
                                   apply=args.apply, limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
